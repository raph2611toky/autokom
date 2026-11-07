#!/usr/bin/env python3
"""Explicit, non-destructive transfer of AUTOKOM-managed authoritative BIND zones.

The archive is private and contains TSIG material. It is never published to Git
or Cloudflare. AWS stays unchanged; GCP import refuses pre-existing zone state.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

import autokom_dns_core as core
from autokom_dns_core import DNSError

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_ZONE_BYTES = 1024 * 1024


def _read_secure(path: Path, *, limit: int = MAX_ZONE_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DNSError(f'Fichier DNS absent, non régulier ou lien symbolique : {path}')
    if path.stat().st_size > limit:
        raise DNSError('Fichier DNS anormalement volumineux ; migration refusée.')
    return path.read_bytes()


def _validate_state(data: dict, zone: str) -> dict:
    if not isinstance(data, dict) or data.get('zone') != zone:
        raise DNSError('État DNS source incohérent : nom de zone différent.')
    if data.get('role') != 'primary':
        raise DNSError('Seule une zone primaire AUTOKOM peut être exportée pour promotion sur GCP.')
    core.validate_name(data['ns1'])
    core.validate_ip(data['primary_ip'], 4)
    if data.get('secondary_ip'):
        core.validate_ip(data['secondary_ip'], 4)
        core.validate_name(data['ns2'])
    if not isinstance(data.get('records'), list) or len(data['records']) > 10000:
        raise DNSError('Liste des enregistrements DNS invalide.')
    if not 1 <= int(data['serial']) <= 4294967295:
        raise DNSError('Numéro SOA invalide.')
    for r in data['records']:
        normalized = core.normalize_record(zone, r['type'], r['name'], r['value'], r['ttl'], r.get('priority'))
        if r != normalized:
            raise DNSError('Enregistrement source non canonique ; migration conservatrice refusée.')
    return data


def _validate_key(content: bytes, zone: str) -> None:
    text = content.decode('ascii')
    # Do not execute or include arbitrary snippets from the import archive.
    wanted = re.escape('autokom-' + zone)
    if not re.fullmatch(r'\s*key\s+"' + wanted +
                        r'"\s*\{\s*algorithm\s+hmac-sha256;\s*secret\s+"[A-Za-z0-9+/=]+";\s*\};\s*',
                        text, re.I):
        raise DNSError('Clé TSIG exportée inattendue, ne sera pas installée.')


def export_zone(zone: str, archive: str) -> None:
    core.need_root()
    zone = core.validate_zone(zone)
    path = Path(archive).resolve()
    if path.exists() or path.is_symlink():
        raise DNSError('L’archive existe déjà : aucun remplacement automatique.')
    if not path.parent.is_dir():
        raise DNSError('Créer au préalable le répertoire privé de sauvegarde DNS.')
    if path.parent.stat().st_mode & 0o077:
        raise DNSError('Répertoire de sauvegarde non privé (chmod 700 requis).')
    state_bytes = _read_secure(core.state_path(zone))
    data = _validate_state(json.loads(state_bytes), zone)
    source_zone = _read_secure(core.bind_path(zone))
    if source_zone.decode('utf-8') != core.zone_text(data):
        raise DNSError('Zone BIND différente de l’état AUTOKOM : sauvegarder et examiner les modifications manuelles.')
    key = _read_secure(core.tsig_path(zone), limit=8192)
    _validate_key(key, zone)
    manifest = {'schema': 1, 'zone': zone, 'source_role': 'primary',
                'sha256': {name: hashlib.sha256(blob).hexdigest() for name, blob in
                           {'state.json': state_bytes, 'zone.db': source_zone, 'tsig.key': key}.items()}}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as target:
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr('manifest.json', json.dumps(manifest, sort_keys=True))
                z.writestr('state.json', state_bytes)
                z.writestr('zone.db', source_zone)
                z.writestr('tsig.key', key)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    print(f'  ✓ Export DNS privé : {path} · {len(data["records"])} enregistrements, SOA {data["serial"]}')
    print('  ! Contient une clé TSIG : transfert SSH chiffré, ne pas placer dans Git.')


def inspect_archive(archive: str, *, zone: str) -> tuple[dict, bytes]:
    zone = core.validate_zone(zone)
    path = Path(archive)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise DNSError('Archive DNS absente, trop grande ou lien symbolique.')
    with zipfile.ZipFile(path) as z:
        members = z.infolist()
        required = {'manifest.json', 'state.json', 'zone.db', 'tsig.key'}
        if len(members) != 4 or {m.filename for m in members} != required or any(
                m.is_dir() or m.file_size > MAX_ZONE_BYTES for m in members):
            raise DNSError('Archive DNS inattendue : seuls quatre fichiers connus sont acceptés.')
        blobs = {name: z.read(name) for name in required}
    manifest = json.loads(blobs['manifest.json'])
    if manifest.get('schema') != 1 or manifest.get('zone') != zone or manifest.get('source_role') != 'primary':
        raise DNSError('Manifest de migration DNS non reconnu.')
    for name in required - {'manifest.json'}:
        if manifest.get('sha256', {}).get(name) != hashlib.sha256(blobs[name]).hexdigest():
            raise DNSError(f'Archive DNS altérée : SHA-256 invalide pour {name}.')
    state = _validate_state(json.loads(blobs['state.json']), zone)
    if blobs['zone.db'].decode('utf-8') != core.zone_text(state):
        raise DNSError('Zone source altérée ou différente de la base AUTOKOM.')
    _validate_key(blobs['tsig.key'], zone)
    return state, blobs['tsig.key']



def ensure_authoritative_ufw():
    """Enable only authoritative UDP/TCP 53 on the VM WAN interface.

    Do not touch VPC firewall rules, foreign UFW rules or DNS recursion policy.
    """
    import shutil
    if not shutil.which('ufw'):
        print('  ! UFW absent : vérifier le pare-feu hôte manuellement.')
        return
    status = subprocess.run(['ufw', 'status'], capture_output=True, text=True, timeout=10)
    if status.returncode:
        raise DNSError('Impossible de vérifier UFW après installation BIND.')
    if not re.search(r'(?m)^Status:\s*active\s*$', status.stdout):
        print('  ! UFW inactif : aucune modification, contrôler le pare-feu VPC/SG.')
        return
    route = subprocess.run(['ip', '-4', 'route', 'get', '1.1.1.1'], capture_output=True,
                           text=True, timeout=8)
    match = re.search(r'\bdev\s+([A-Za-z0-9_.:-]+)\b', route.stdout) if route.returncode == 0 else None
    if not match:
        raise DNSError('Interface WAN introuvable : ne pas ouvrir DNS sur toutes les interfaces.')
    interface = match.group(1)
    for protocol in ('udp', 'tcp'):
        # Respect deliberate external DENY/REJECT; separate rules for each
        # interface and protocol; don't conflate wg0 with the WAN.
        all_rows = [line for line in status.stdout.splitlines()
                    if re.search(r'\b53/' + protocol + r'\b', line) and '(v6)' not in line]
        rows = [line for line in all_rows if 'on ' + interface in line]
        if any(re.search(r'\b(DENY|REJECT)\b', line) for line in all_rows):
            raise DNSError(f'UFW : refus explicite 53/{protocol} sur {interface}, aucune règle contournée.')
        if any(re.search(r'\bALLOW\b', line) for line in rows):
            print(f'  ✓ UFW 53/{protocol} déjà autorisé sur {interface}')
            continue
        cmd = ['ufw', 'allow', 'in', 'on', interface, 'to', 'any', 'port', '53', 'proto', protocol]
        changed = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if changed.returncode:
            raise DNSError(f'Échec UFW 53/{protocol} sur {interface}; vérifier la règle manuellement.')
        print(f'  ✓ UFW 53/{protocol} autorisé sur {interface}')


def import_zone(*, zone: str, archive: str, public_ip: str, listen_ip: str,
                apply: bool, confirmed: bool = False) -> None:
    """Promote an exported primary to a *new*, empty GCP BIND installation."""
    from autokom_dns import bound_options, _read_named_options, local_listen_ips, _key_owner, atomic_batch, reload_bind
    zone = core.validate_zone(zone)
    state, key = inspect_archive(archive, zone=zone)
    public_ip = core.validate_ip(public_ip, 4)
    if not ipaddress.ip_address(public_ip).is_global:
        raise DNSError('IP DNS publique non routable.')
    listen_ip = core.validate_ip(listen_ip, 4)
    if listen_ip not in local_listen_ips():
        raise DNSError('L’IP BIND d’écoute doit appartenir à une interface locale GCP.')
    if state.get('secondary_ip') and state['secondary_ip'] != public_ip:
        raise DNSError('La zone source déclare un secondaire externe : migration automatique refusée. '
                         'Effectuer un plan de transfert des NS avant promotion.')
    # GCP was perhaps a secondary of AWS: promote it; old AWS must not remain
    # in the SOA or the list of transfer targets once expired.
    target = dict(state, primary_ip=public_ip, secondary_ip=None, ns2=None)
    core.bump_serial(target)
    content = core.zone_text(target)
    all_paths = (core.state_path(zone), core.bind_path(zone), core.tsig_path(zone),
                 core.managed_zone_conf(zone))
    for path in all_paths:
        if path.exists() or path.is_symlink():
            raise DNSError(f'La destination contient déjà {path} ; aucun écrasement. '
                             'Examiner la configuration existante avant migration.')
    print(f'  › Import {zone} : {len(target["records"])} enregistrements conservés')
    print(f'  › Nouveau primaire : {public_ip} · écoute locale {listen_ip}')
    print(f'  › SOA {state["serial"]} → {target["serial"]} ; NS principal {target["ns1"]}')
    print('  ! Le DNS AWS et Cloudflare ne seront pas modifiés par cette commande.')
    if not apply:
        print('  ✓ Prévisualisation DNS : aucune modification.'); return
    core.need_root()
    if not confirmed:
        raise DNSError('Confirmation explicite de migration requise.')
    if not Path('/usr/sbin/named-checkzone').exists() and not __import__('shutil').which('named-checkzone'):
        raise DNSError('BIND9 non installé : sudo autokom dns install --apply')
    core.check_zone(zone, content)
    opt_path, old_options = _read_named_options()
    options = bound_options(old_options, listen_ip)
    local_include = core.LOCAL_INCLUDE.read_text(encoding='utf-8') if core.LOCAL_INCLUDE.exists() else ''
    if re.search(r'(?m)^\s*zone\s+["\']', local_include):
        raise DNSError('Zones BIND étrangères détectées : import refusé sans inventaire.')
    managed, local = core.ensure_includes(zone)
    new = {
        opt_path: (options, 0o644),
        core.MANAGED_INCLUDE: (managed, 0o644),
        core.LOCAL_INCLUDE: (local, 0o644),
        core.managed_zone_conf(zone): (core.render_bind_zone_statement(target), 0o644),
        core.bind_path(zone): (content, 0o644),
        core.state_path(zone): (json.dumps(target, indent=2) + '\n', 0o640),
    }
    key_path = core.tsig_path(zone)
    core.atomic_write(key_path, key.decode('ascii'), mode=0o640)
    try:
        _key_owner(key_path)
        def _validate():
            core.verify_named_conf()
            reload_bind(zone, first_start=True)
        atomic_batch(new, _validate)
    except BaseException:
        key_path.unlink(missing_ok=True)
        raise
    ensure_authoritative_ufw()
    print(f'  ✓ Zone autoritaire {zone} restaurée sur GCP ; vérifier UDP/TCP 53 depuis Internet.')
    print('  ! Conserver AWS tant que la réponse SOA sur ns1.ikom.mg n’est pas validée de l’extérieur.')
