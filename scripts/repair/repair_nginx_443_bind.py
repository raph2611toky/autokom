#!/usr/bin/env python3
"""AUTOKOM: explicit, transactional recovery of an IPv4:443 VPN/listener collision.

Never changes UFW, GCP rules, Node.js or a 3rd party process.  This CLI is
intentional opt-in, not an implicit step of `autokom install`.

Usage: sudo python3 scripts/repair/repair_nginx_443_bind.py --check
       sudo python3 scripts/repair/repair_nginx_443_bind.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

LIB = Path(__file__).resolve().parents[1] / 'lib'
sys.path.insert(0, str(LIB))
from autokom_nginx_runtime import listeners
from autokom_nginx_bind_policy import STATE as POLICY_STATE, addresses as policy_addresses

NGINX = Path('/etc/nginx')
BACKUPS = Path('/var/backups/autokom')
PROVENANCE = re.compile(r'^# configuration file (/.+):$')
LISTEN = re.compile(r'^(\s*)listen\s+(?:(?:0\.0\.0\.0):)?443(\s+[^;]+);(\s*(?:#.*)?)$')
LISTEN_443_ANY = re.compile(r'^\s*listen\s+\S*443\b[^;]*;')
IPV6 = re.compile(r'^\s*listen\s+\[::\]:443\b[^;]*;')
SAFE_PATHS = (Path('/etc/nginx/sites-available'), Path('/etc/nginx/sites-enabled'), Path('/etc/nginx/conf.d'))


class RepairError(RuntimeError):
    pass


def run(argv, timeout=25):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepairError(f'Commande indisponible/expirée: {argv[0]}') from exc
    if p.returncode:
        # nginx -T can contain secrets. Never print stdout/stderr here.
        raise RepairError(f'{argv[0]} a échoué (code {p.returncode}); voir journalctl -u nginx')
    return p.stdout


def main_ipv4():
    try:
        route = json.loads(run(['ip', '-j', '-4', 'route', 'get', '1.1.1.1']))
        entry = route[0]
        dev = entry.get('dev', '')
        address = ipaddress.IPv4Address(entry.get('prefsrc') or entry.get('src'))
    except (ValueError, TypeError, IndexError, KeyError, AttributeError) as exc:
        raise RepairError('Adresse IPv4 de l’interface WAN non vérifiable') from exc
    if dev in ('lo', 'docker0') or dev.startswith(('br-', 'wg', 'tun', 'tailscale', 'veth')):
        raise RepairError('Interface routée non compatible : ' + dev)
    if address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local:
        raise RepairError('Adresse IPv4 non utilisable : ' + str(address))
    return str(address), dev


def forbidden_listener(found, target_ip):
    """Only permit foreign TCP/443 on an *other specific* address (e.g. wg0)."""
    for item in found.get(443, []):
        address = item.address
        if item.name == 'nginx':
            continue
        if address in ('*:443', '0.0.0.0:443', '[::]:443', ':::443'):
            return item
        if address.rsplit(':', 1)[0].strip('[]') in (target_ip, '127.0.0.1', '::'):
            return item
    return None


def active_files(dump):
    """Files actually loaded by Nginx, not arbitrary *.conf glob matches."""
    names = []
    current = None
    for line in dump.splitlines():
        match = PROVENANCE.match(line)
        if match:
            current = Path(match.group(1))
            names.append(current)
    if not names:
        raise RepairError('nginx -T sans provenance : impossible de garantir les fichiers actifs')
    return list(dict.fromkeys(names))


def rewritten(original, target_ip):
    """Pure transformation: keep existing nginx flags/comments on two IPv4 sockets."""
    result = []
    count = 0
    for line in original.splitlines(keepends=True):
        raw = line.rstrip('\r\n')
        m = LISTEN.fullmatch(raw)
        if m:
            indent, flags, comment = m.groups()
            if 'ssl' not in flags.split():
                raise RepairError('Directive 443 inattendue sans SSL : modification refusée')
            if 'ipv6only=' in flags or 'bind' in flags.split() or 'reuseport' in flags.split():
                raise RepairError('Options listen non standard : modification refusée')
            ending = '\r\n' if line.endswith('\r\n') else ('\n' if line.endswith('\n') else '')
            result.append(f'{indent}listen {target_ip}:443{flags};{comment}{ending}')
            result.append(f'{indent}listen 127.0.0.1:443{flags};{ending}')
            count += 1
        else:
            if IPV6.match(raw):
                if re.search(r'\bipv6only\s*=\s*off\b', raw):
                    raise RepairError('IPv6 443 ipv6only=off : conflit possible, refus')
            elif LISTEN_443_ANY.match(raw) and not raw.lstrip().startswith('#'):
                # Explicit binds and non-standard variants cannot safely be translated.
                if not (f'{target_ip}:443' in raw or '127.0.0.1:443' in raw):
                    raise RepairError('Directive listen TCP/443 non standard : ' + raw.strip()[:150])
            result.append(line)
    return ''.join(result), count


def plan(dump, target_ip):
    changes = {}
    for published in active_files(dump):
        try:
            path = published.resolve(strict=True)
        except OSError as exc:
            raise RepairError(f'Fichier Nginx non résolu: {published}') from exc
        if path in changes:
            continue
        if not any(path.is_relative_to(base) for base in SAFE_PATHS):
            # Inspect but don't modify arbitrary nginx.conf, package configs, snippets.
            source = path.read_text(encoding='utf-8')
            _, n = rewritten(source, target_ip)
            if n:
                raise RepairError(f'Listen 443 global dans {path}; intervention manuelle requise')
            continue
        if path.is_symlink() or not path.is_file():
            raise RepairError(f'Cible Nginx non fiable : {path}')
        info = path.stat()
        if info.st_size > 1024 * 1024 or info.st_mode & 0o002:
            raise RepairError(f'Fichier Nginx trop grand/ou modifiable par tous : {path}')
        source = path.read_text(encoding='utf-8')
        updated, count = rewritten(source, target_ip)
        if count:
            changes[path] = (source, updated, info)
    if not changes:
        raise RepairError('Aucune directive IPv4 listen 443 globale trouvée parmi les fichiers actifs; autre diagnostic nécessaire')
    return changes


def write_atomic(path, body, info):
    fd, candidate = tempfile.mkstemp(prefix='.autokom-443-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(candidate, info.st_mode & 0o777)
        os.chown(candidate, info.st_uid, info.st_gid)
        os.replace(candidate, path)
    finally:
        if os.path.lexists(candidate):
            os.unlink(candidate)


def backup(changes):
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = BACKUPS / ('nginx-443-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid()))
    dest.mkdir(mode=0o700)
    records = []
    for index, (path, (source, _, info)) in enumerate(changes.items()):
        saved = dest / f'{index:03d}.conf'
        saved.write_text(source, encoding='utf-8')
        saved.chmod(0o600)
        records.append({'original_path':str(path), 'backup':saved.name,
                        'sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                        'uid':info.st_uid, 'gid':info.st_gid, 'mode':info.st_mode & 0o777})
    (dest / 'manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    (dest / 'manifest.json').chmod(0o600)
    return dest


def listening_nginx(found, address):
    return any(item.name == 'nginx' and item.address.rsplit(':',1)[0] == address
               for item in found.get(443, []))


def verify_local_domains():
    # The selected AUTOKOM domain state, not hardcoded user domains.
    from autokom_ingress_health import managed_domains, verify_observability
    from observability_config import load
    domains = managed_domains(load())
    requested = [(kind, domain) for kind, domain in domains.items() if domain]
    if not requested:
        print('  ! Aucun domaine AUTOKOM enregistré: test HTTPS/SNI applicatif non effectué')
        return
    verify_observability(requested)
    print('  ✓ HTTPS/SNI et HTTP→HTTPS validés : ' + ', '.join(domain for _, domain in requested))


def save_policy(target_ip):
    """Persist policy only *after* HTTP+TLS local verification succeeds."""
    path = POLICY_STATE
    if path.is_symlink():
        raise RepairError('Politique HTTPS symbolique interdite')
    if path.exists():
        try:
            previous = policy_addresses()
        except Exception as exc:
            raise RepairError('Politique HTTPS antérieure corrompue : refus') from exc
        if previous != (target_ip, '127.0.0.1'):
            raise RepairError('Politique HTTPS antérieure différente : intervention requise')
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps({'schema': 1, 'port': 443,
                          'addresses': [target_ip, '127.0.0.1']}, indent=2)
    fd, candidate = tempfile.mkstemp(prefix='.bind-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(candidate, 0o600)
        os.replace(candidate, path)
    finally:
        if os.path.lexists(candidate):
            os.unlink(candidate)


def apply(changes, target_ip):
    if os.geteuid() != 0:
        raise RepairError('--apply exige sudo')
    # Stop if another process already occupies the required sockets.
    collision = forbidden_listener(listeners(), target_ip)
    if collision:
        raise RepairError(f'Impossible de binder {target_ip}/127.0.0.1:443 : {collision.name} PID={collision.pid} sur {collision.address}')
    snapshot = backup(changes)
    print('  ✓ Sauvegarde : ' + str(snapshot))
    touched = []
    try:
        for path, (source, target, info) in changes.items():
            if path.read_text(encoding='utf-8') != source:
                raise RepairError('Modification concurrente détectée : ' + str(path))
            write_atomic(path, target, info)
            touched.append(path)
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        time.sleep(0.6)
        bound = listeners()
        if not (listening_nginx(bound, target_ip) and listening_nginx(bound, '127.0.0.1')):
            # Reload may silently retain old workers/listeners after bind errors.
            if forbidden_listener(bound, target_ip):
                raise RepairError('Conflit détecté après reload; aucun service externe interrompu')
            print('  › Reload sans sockets HTTPS attendus : restart Nginx contrôlé une seule fois')
            run(['systemctl', 'restart', 'nginx'])
            time.sleep(0.6)
            bound = listeners()
        if not (listening_nginx(bound, target_ip) and listening_nginx(bound, '127.0.0.1')):
            raise RepairError('Nginx ne possède pas simultanément les sockets HTTPS attendus')
        verify_local_domains()
        save_policy(target_ip)
    except Exception as exc:
        print('  ✗ Validation échouée : restauration des fichiers Nginx précédents')
        for path in touched:
            source, _, info = changes[path]
            write_atomic(path, source, info)
        try:
            run(['nginx', '-t'])
            run(['systemctl', 'reload', 'nginx'])
        except RepairError:
            print('  ! Rollback sur disque effectué; Nginx à vérifier manuellement: sudo nginx -t')
        raise RepairError(f'Correction annulée: {exc}; sauvegardes dans {snapshot}') from exc
    print('  ✓ Nginx écoute TCP/443 sur ' + target_ip + ' et 127.0.0.1')
    print('  ✓ Aucun processus Node.js/WireGuard ni règle UFW modifié')
    print('  ! Accès public GCP non vérifié : tester depuis un poste extérieur')
    print('  ! Futurs vhosts émettant "listen 443 ssl" doivent respecter ce partitionnement')
    return str(snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--check', action='store_true')
    choice.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        target_ip, nic = main_ipv4()
        print(f'AUTOKOM / Collision HTTPS — IP principale {target_ip} sur {nic}')
        existing = listeners()
        for item in existing.get(443, []):
            print(f'  › TCP/443 : {item.address}, {item.name} PID {item.pid or "?"}')
        offender = forbidden_listener(existing, target_ip)
        if offender:
            raise RepairError(f'Socket HTTPS requis déjà occupé : {offender.address} ({offender.name}); aucune modification')
        try:
            files = plan(run(['nginx', '-T']), target_ip)
        except RepairError as exc:
            if str(exc).startswith('Aucune directive IPv4') and policy_addresses() == (target_ip, '127.0.0.1'):
                bound = listeners()
                if listening_nginx(bound, target_ip) and listening_nginx(bound, '127.0.0.1'):
                    print('  ✓ Déjà corrigé : TLS sur IP WAN privée et loopback, politique persistante')
                    return 0
            raise
        for path, (_, _, info) in files.items():
            print('  › Adapter : ' + str(path) + (' [non AUTOKOM, sauvegarde obligatoire]' if not Path(path).name.startswith(('autokom-', '00-autokom-')) else ''))
        print('  › TCP/80 et pare-feu UFW inchangés')
        if args.check:
            print('  ✓ Simulation terminée, aucun fichier modifié. Utiliser --apply pour réparer.')
            return 0
        apply(files, target_ip)
        return 0
    except (RepairError, OSError, ValueError) as exc:
        print('  ✗ ' + str(exc), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
