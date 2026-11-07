#!/usr/bin/env python3
"""AUTOKOM DNS: BIND9 authority for delegated IKOM zones + Cloudflare apex.

No cloud provider credentials committed; no automatic registrar/delegation updates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time

import autokom_dns_core as core
from autokom_dns_core import DNSError

ROOT = Path(__file__).resolve().parents[2]
CFG = core.CONF_ROOT
STATE = core.STATE_ROOT


def good(msg): print(f'  \033[32m✓\033[0m {msg}')
def info(msg): print(f'  › {msg}')
def title(msg): print(f'\n  \033[36m◈ AUTOKOM · DNS · {msg}\033[0m\n')


def choose(label, choices, default=1):
    from menu import pick
    return pick(label, choices, default=default, cancel_label='Annuler')


def ask(label, default=''):
    from menu import ask as menu_ask
    return menu_ask(label, default=default)


def confirmation(text, expected='CONFIGURER', yes=False):
    if yes:
        return True
    if not sys.stdin.isatty():
        raise DNSError('Confirmation interactive impossible : fournir --yes dans un playbook Ansible validé.')
    entered = input('  ' + text + f' · saisir {expected} : ').strip()
    return entered == expected


def run(args, *, timeout=40, input_text=None):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, input=input_text, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DNSError('Commande système indisponible : ' + Path(args[0]).name) from exc


def privileged(*, check=False):
    if not check:
        core.need_root()


def atomic_batch(changes: dict[Path, tuple[str, int]], validate):
    """Rollback modified files on validation failure; no overwrite of unrelated paths."""
    old = {}
    for path in changes:
        old[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
    try:
        for path, (text, mode) in changes.items():
            core.atomic_write(path, text, mode=mode)
        validate()
    except Exception:
        for path, previous in reversed(list(old.items())):
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                core.atomic_write(path, previous[0].decode('utf-8'), mode=previous[1])
        raise


def local_listen_ips() -> list[str]:
    p = run(['ip', '-o', '-4', 'addr', 'show', 'scope', 'global'])
    if p.returncode:
        return []
    return [entry for entry in re.findall(r'\binet\s+([0-9.]+)/\d+', p.stdout)]



def route_listen_ip(local_ips=None):
    """Choose the routed VM address (ens4 on GCP), not docker0 or wg0."""
    local_ips = local_ips if local_ips is not None else local_listen_ips()
    p = run(['ip', '-4', 'route', 'get', '1.1.1.1'], timeout=8)
    if p.returncode == 0:
        m = re.search(r'\bsrc\s+([0-9.]+)', p.stdout)
        if m and m.group(1) in local_ips:
            return m.group(1)
    return local_ips[0] if len(local_ips) == 1 else None


def bound_options(text: str, listen_ip: str) -> str:
    """Apply authoritative-only settings to stock-like options, preserve extras.

    Refuse already-customized conflicting security/listen directives. Never disable
    systemd-resolved or rewrite resolv.conf.
    """
    address = core.validate_ip(listen_ip, 4)
    if text.count('options {') != 1:
        raise DNSError('named.conf.options personnalisé : configuration manuelle requise (refus d’écraser).')
    begin_marker, end_marker = '/* AUTOKOM-DNS-SECURITY-BEGIN */', '/* AUTOKOM-DNS-SECURITY-END */'
    managed = ('\n    ' + begin_marker + '\n'
               '    recursion no;\n'
               '    allow-recursion { none; };\n'
               '    allow-query-cache { none; };\n'
               '    allow-transfer { none; };\n'
               f'    listen-on port 53 {{ 127.0.0.1; {address}; }};\n'
               '    listen-on-v6 { none; };\n'
               '    allow-query { any; };\n'
               '    ' + end_marker + '\n')
    if begin_marker in text or end_marker in text:
        if text.count(begin_marker) != 1 or text.count(end_marker) != 1:
            raise DNSError('Bloc AUTOKOM d’options BIND incohérent.')
        return re.sub(re.escape(begin_marker) + r'.*?' + re.escape(end_marker),
                      managed.strip(), text, count=1, flags=re.S)
    # Refuse existing custom settings (except Ubuntu stock's listen-on-v6 any).
    stock = re.sub(r'(?m)^\s*listen-on-v6\s*\{\s*any;\s*\};\s*$', '', text)
    if re.search(r'\b(?:forwarders|forward|view)\b', stock):
        raise DNSError('BIND a une configuration de résolveur existante : refus de modifier les options globales.')
    for key in ('recursion', 'allow-recursion', 'allow-query-cache',
                'allow-transfer', 'listen-on', 'allow-query'):
        if re.search(r'\b' + re.escape(key) + r'\b\s*(?:port\s+\d+\s*)?(?:\{|\b(?:yes|no)\b)', stock):
            raise DNSError(f'Option BIND existante « {key} » : refus de la remplacer automatiquement.')
    text = re.sub(r'(?m)^\s*listen-on-v6\s*\{\s*any;\s*\};\s*$', '', text)
    pos = text.index('options {') + len('options {')
    return text[:pos] + managed + text[pos:]


def _read_named_options():
    path = core.BIND_ROOT / 'named.conf.options'
    if not path.is_file():
        raise DNSError('BIND non installé ou named.conf.options absent. Exécuter autokom dns install --apply.')
    return path, path.read_text(encoding='utf-8')


def _key_owner(path: Path):
    # AppArmor permits /etc/bind/**; ownership root:bind for BIND read access.
    try:
        import grp
        bind_gid = grp.getgrnam('bind').gr_gid
    except KeyError as exc:
        raise DNSError('Groupe BIND manquant : paquet bind9 absent.') from exc
    os.chown(path, 0, bind_gid)
    os.chmod(path, 0o640)


def _ensure_tsig_primary(zone: str):
    path = core.tsig_path(zone)
    if path.is_file():
        _key_owner(path)
        return False
    if not shutil.which('tsig-keygen'):
        raise DNSError('tsig-keygen indisponible.')
    p = run(['tsig-keygen', '-a', 'hmac-sha256', 'autokom-' + zone], timeout=10)
    if p.returncode or not p.stdout.startswith('key '):
        raise DNSError('Génération TSIG impossible.')
    core.atomic_write(path, p.stdout, mode=0o640)
    _key_owner(path)
    return True


def _zone_state(zone, env, role, primary_ip, secondary_ip, ns1, ns2):
    if role == 'secondary' and not secondary_ip:
        raise DNSError('Le serveur DNS secondaire exige son IP publique (--secondary-ip).')
    return dict(zone=core.validate_zone(zone), environment=env,
                role=role, primary_ip=core.validate_ip(primary_ip, 4),
                secondary_ip=core.validate_ip(secondary_ip, 4) if secondary_ip else None,
                ns1=core.validate_name(ns1), ns2=core.validate_name(ns2) if secondary_ip else None,
                serial=0, records=[])


def _service_name():
    for unit in ('named.service', 'bind9.service'):
        p = run(['systemctl', 'show', '-p', 'LoadState', '--value', unit], timeout=6)
        if p.returncode == 0 and p.stdout.strip() == 'loaded':
            return unit
    return 'named.service'


def reload_bind(zone, *, first_start=False):
    unit = _service_name()
    active = run(['systemctl', 'is-active', '--quiet', unit])
    if active.returncode == 0:
        p = run(['rndc', 'reconfig'])
        if p.returncode:
            raise DNSError('BIND n’a pas accepté la reconfiguration (rndc reconfig).')
        p = run(['rndc', 'reload', zone])
        if p.returncode:
            raise DNSError('La zone BIND n’a pas été rechargée correctement.')
    else:
        p = run(['systemctl', 'enable', '--now', unit], timeout=45)
        if p.returncode:
            raise DNSError('BIND n’a pas démarré. Vérifier journalctl -u named et le port 53.')


def bind_setup(args):
    role = args.role
    zone = args.zone
    env = args.environment
    if not role:
        if not sys.stdin.isatty(): raise DNSError('Préciser --role primary ou secondary.')
        i = choose('Mode DNS BIND9', ['Primaire autoritaire · ce serveur', 'Secondaire autoritaire · ce serveur'])
        if not i: return 0
        role = ('primary', 'secondary')[i-1]
    if not zone:
        if not sys.stdin.isatty(): raise DNSError('Préciser --zone.')
        zone = ask('Sous-zone à déléguer', 'infra.ikom.mg')
    zone = core.validate_zone(zone)
    if not env:
        if sys.stdin.isatty():
            i = choose('Environnement de la zone', ['Production', 'Préproduction', 'Développement'])
            if not i: return 0
            env = ('production', 'staging', 'development')[i-1]
        else: env = 'production'
    from domain_validation import instance_public_ipv4
    provider, detected_ip = instance_public_ipv4()
    primary_ip = args.primary_ip or (detected_ip if role == 'primary' else '')
    secondary_ip = args.secondary_ip
    ns1, ns2 = args.ns1 or 'ns1.ikom.mg', args.ns2 or 'ns2.ikom.mg'
    desired_state = core.state_path(zone)
    previous = core.load_state(zone) if desired_state.is_file() else None
    # Never silently remove an already configured secondary when rerunning setup.
    if previous and previous.get('secondary_ip') and not secondary_ip:
        secondary_ip = previous['secondary_ip']
        ns2 = previous['ns2']
    if not primary_ip and sys.stdin.isatty():
        primary_ip = ask('IPv4 publique du DNS primaire', detected_ip or '')
    if not primary_ip:
        raise DNSError('Adresse publique du primaire obligatoire (--primary-ip).')
    if not secondary_ip and role == 'primary' and not previous and sys.stdin.isatty():
        i = choose('Serveur DNS secondaire', [
            'Aucun secondaire pour le moment (défaut)',
            'J’ai déjà un serveur secondaire disponible',
        ])
        if not i: return 0
        if i == 2:
            secondary_ip = ask('IP publique du DNS secondaire')
            if not secondary_ip:
                raise DNSError('IP publique GCP nécessaire pour activer un secondaire.')
    if role == 'secondary' and not secondary_ip:
        raise DNSError('Préciser --secondary-ip pour configurer un serveur secondaire.')
    state = _zone_state(zone, env, role, primary_ip, secondary_ip, ns1, ns2)
    local_ips = local_listen_ips()
    listen_ip = args.listen_ip or route_listen_ip(local_ips)
    if not listen_ip:
        raise DNSError('IP locale de routage indéterminée. Préciser --listen-ip (IP présente sur ens4/eth0).')
    listen_ip = core.validate_ip(listen_ip, 4)
    if listen_ip not in local_ips:
        raise DNSError('L’IP --listen-ip doit être configurée sur une interface locale (sur GCP/AWS, utiliser l’IP privée NAT).')
    existing = bool(previous)
    activate_secondary = False
    if existing:
        for key in ('role', 'primary_ip', 'ns1', 'environment'):
            if previous.get(key) != state.get(key):
                raise DNSError('Zone existante avec configuration différente : migration DNS manuelle requise.')
        old_secondary = previous.get('secondary_ip')
        new_secondary = state.get('secondary_ip')
        if old_secondary and (old_secondary != new_secondary or previous.get('ns2') != state.get('ns2')):
            raise DNSError('Secondaire existant différent : migration DNS manuelle requise.')
        if old_secondary and not new_secondary:
            raise DNSError('Un secondaire existant ne peut pas être supprimé par setup.')
        if not old_secondary and new_secondary:
            if role != 'primary':
                raise DNSError('La mise à niveau vers deux NS doit être initiée sur le primaire.')
            activate_secondary = True
            state = previous.copy()
            state['secondary_ip'] = new_secondary
            state['ns2'] = ns2
            core.bump_serial(state)
        else:
            state = previous
    else:
        core.bump_serial(state)
    title('PLAN DE CONFIGURATION')
    for k, v in [('Zone', zone), ('Environnement', env), ('Rôle', role),
                 ('DNS primaire autoritaire', primary_ip), ('DNS secondaire autoritaire', secondary_ip or 'non configuré'),
                 ('Écoute BIND locale', listen_ip), ('Clé TSIG', str(core.tsig_path(zone))),
                 ('État de la zone', 'ajout secondaire GCP' if activate_secondary else ('existe déjà' if existing else 'nouvelle'))]:
        info(f'{k:<24} {v}')
    if not secondary_ip:
        info('MODE PRÉPARATION : primaire seul, NS1 uniquement, aucun transfert TSIG / NOTIFY actif.')
        info('Vérifier l’état réel de la délégation Cloudflare et prévoir un DNS secondaire fiable.')
    info('Pas de modification de dot.mg, Cloudflare, Nginx, Docker ou MySQL.')
    if args.check or (not args.apply and not sys.stdin.isatty()):
        good('Prévisualisation terminée, aucun changement.'); return 0
    if not confirmation('Installer / appliquer la zone DNS ?', yes=args.yes):
        info('Annulé sans modification.'); return 0
    core.need_root()
    if not shutil.which('named-checkconf'):
        raise DNSError('Installer BIND9 via autokom dns install avant setup.')
    opt_path, opt_orig = _read_named_options()
    opt_new = bound_options(opt_orig, listen_ip)
    # Only bind authoritative port on local interface IP; never touch OS resolver.
    if role == 'secondary' and not core.tsig_path(zone).is_file():
        raise DNSError('Clé TSIG secondaire absente : transférer de façon sécurisée depuis AWS vers ' + str(core.tsig_path(zone)))
    created_key = False
    if role == 'primary':
        created_key = _ensure_tsig_primary(zone)
    else:
        _key_owner(core.tsig_path(zone))
    if core.LOCAL_INCLUDE.exists():
        local_existing = core.LOCAL_INCLUDE.read_text(encoding='utf-8')
        if re.search(r'(?m)^\s*zone\s+[\"\']', local_existing):
            raise DNSError('BIND sert déjà des zones externes. Inspecter named.conf.local avant de changer les options globales.')
    managed, local = core.ensure_includes(zone)
    zone_file = core.bind_path(zone)
    changes = {
        opt_path: (opt_new, 0o644),
        core.MANAGED_INCLUDE: (managed, 0o644),
        core.LOCAL_INCLUDE: (local, 0o644),
        core.managed_zone_conf(zone): (core.render_bind_zone_statement(state), 0o644),
    }
    if role == 'primary':
        content = core.zone_text(state)
        core.check_zone(zone, content)
        changes[zone_file] = (content, 0o644)
    try:
        if role == 'secondary':
            cache = Path('/var/cache/bind/autokom')
            cache.mkdir(parents=True, exist_ok=True, mode=0o750)
            try:
                import grp
                os.chown(cache, 0, grp.getgrnam('bind').gr_gid)
            except KeyError:
                raise DNSError('Groupe bind introuvable.')
        # Apply state together with named configuration, roll back if BIND rejects.
        changes[desired_state] = (json.dumps(state, indent=2) + '\n', 0o644)
        def _validate_and_reload():
            core.verify_named_conf()
            reload_bind(zone, first_start=True)
        atomic_batch(changes, _validate_and_reload)
    except Exception:
        if created_key:
            core.tsig_path(zone).unlink(missing_ok=True)
        raise
    good('BIND configuré et zone contrôlée.')
    info('Autoriser UDP/TCP 53 sur le pare-feu hôte et le VPC/SG du fournisseur cloud.')
    if not secondary_ip:
        info('Primaire seul : prévoir un secondaire fiable pour la disponibilité de la zone ' + zone + '.')
        info('Configurer un secondaire indépendant avant de retirer définitivement l’ancien DNS.')
    else:
        info('Ouvrir UDP/TCP 53 sur les pare-feu hôte et cloud des deux serveurs.')
        info('Après contrôle des deux serveurs : délégation Cloudflare ' + zone + ' vers ' + ns1 + ' et ' + ns2)
    return 0


def install(args):
    title('INSTALLATION BIND9')
    info('Ansible installe bind9, bind9-utils et dnsutils, sans toucher Nginx, Django ou MySQL.')
    info('Ne configure aucune zone avant autokom dns setup.')
    if not (args.check or args.apply or sys.stdin.isatty()):
        good('Plan uniquement.'); return 0

    play = ROOT / 'ansible/playbooks/dns.yml'
    inventory = ROOT / 'ansible/inventories/local/hosts.ini'
    role_dir = ROOT / 'ansible/roles/dns_server'
    if not play.is_file():
        raise DNSError('Playbook DNS absent.')
    if not (role_dir / 'tasks/main.yml').is_file():
        raise DNSError('Rôle BIND introuvable : ' + str(role_dir))
    if not inventory.is_file():
        raise DNSError('Inventaire Ansible AUTOKOM absent : ' + str(inventory))
    if not shutil.which('ansible-playbook'):
        raise DNSError('Ansible introuvable : utiliser les prérequis AUTOKOM Core.')

    # Resolves roles reliably from any current working directory, including /root.
    env = os.environ.copy()
    current = env.get('ANSIBLE_ROLES_PATH', '')
    env['ANSIBLE_ROLES_PATH'] = str(ROOT / 'ansible/roles') + (os.pathsep + current if current else '')
    cmd = ['ansible-playbook', '-i', str(inventory), str(play)]
    try:
        syntax = subprocess.run(cmd + ['--syntax-check'], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=40, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DNSError('Contrôle de syntaxe Ansible indisponible.') from exc
    if syntax.returncode:
        raise DNSError('Playbook DNS invalide : ' + (syntax.stderr or syntax.stdout)[-800:])
    good('Playbook Ansible et rôle dns_server vérifiés.')
    if args.check:
        good('Contrôle sans modification : prêt à installer BIND9.'); return 0
    if not confirmation('Installer BIND9 ?', expected='INSTALLER', yes=args.yes):
        info('Installation annulée.'); return 0
    core.need_root()
    from autokom_app_manager import operation, phase, call, AppError
    try:
        with operation('dns-install'):
            with phase('BIND9 · installation via Ansible'):
                call(cmd, cwd=ROOT, timeout=600, env=env)
    except AppError as exc:
        raise DNSError('Installation DNS interrompue : ' + str(exc)) from exc
    good('BIND9 disponible. Configurer ensuite la zone via autokom dns setup.')
    return 0


def bind_record(args):
    zone = core.validate_zone(args.zone)
    state = core.load_state(zone)
    if state['role'] != 'primary' and args.action != 'list':
        raise DNSError('Les modifications DNS sont autorisées sur le primaire uniquement.')
    records = state.get('records', [])
    if args.action == 'list':
        title('ENREGISTREMENTS · ' + zone)
        for i, rec in enumerate(records, 1):
            print(f'  {i:>2}. {rec["type"]:<5} {rec["name"]:<43} {rec["value"][:78]}')
        if not records: info('Aucun enregistrement supplémentaire (SOA/NS gérés automatiquement).')
        return 0
    if args.action == 'add':
        record = core.normalize_record(zone, args.type, args.name, args.value, args.ttl, args.priority)
        core.add_record(state, record)
    else:
        if args.index is None: raise DNSError('Spécifier --index pour la suppression.')
        core.delete_record(state, args.index)
    content = core.zone_text(state)
    title('PLAN DES MODIFICATIONS · ' + zone)
    info(f'Nouvelle version SOA : {state["serial"]}')
    core.check_zone(zone, content) if shutil.which('named-checkzone') else info('named-checkzone requis avant application')
    if args.check or not args.apply:
        good('Simulation : aucune écriture. Ajouter --apply pour valider.'); return 0
    if not confirmation('Mettre à jour et recharger la zone ?', expected='APPLIQUER', yes=args.yes):
        return 0
    core.need_root()
    core.check_zone(zone, content)
    # Zone and JSON are rewritten only after BIND accepts the updated zone file.
    changes = {core.bind_path(zone): (content, 0o644),
               core.state_path(zone): (json.dumps(state, indent=2) + '\n', 0o644)}
    def _validate_reload():
        core.check_zone(zone, core.bind_path(zone).read_text())
        reload_bind(zone)
    try:
        atomic_batch(changes, _validate_reload)
    except Exception:
        # Reload the restored content if the previous request was partially applied.
        run(['rndc', 'reload', zone])
        raise
    good('Enregistrement modifié, zone rechargée.')
    return 0


def list_zones(only_environments=False):
    title('ENVIRONNEMENTS DNS' if only_environments else 'ZONES DNS')
    directory = STATE / 'zones'
    for path in sorted(directory.glob('*.json')) if directory.exists() else []:
        item = json.loads(path.read_text())
        stage = ' · primaire seul / secondaire en attente' if item['role'] == 'primary' and not item.get('secondary_ip') else ''
        print(f'  ✓ {item["zone"]:<30} {item["environment"]:<14} {item["role"]:<10} SOA {item["serial"]}{stage}')
    info('La zone racine ikom.mg est administrée sur Cloudflare.')
    return 0


def status():
    title('ÉTAT DNS')
    for command in ['named-checkconf', 'named-checkzone', 'dig', 'ansible-playbook']:
        info(f'{command:<20} ' + ('disponible' if shutil.which(command) else 'absent'))
    result = run(['systemctl', 'is-active', _service_name()])
    info('Service BIND           ' + result.stdout.strip())
    listen = run(['ss', '-lntup'])
    if listen.returncode == 0:
        info('Port DNS 53           ' + ('à vérifier avec ss' if ':53 ' in listen.stdout else 'non détecté'))
    return list_zones()


def verify(args):
    zone = core.validate_zone(args.zone)
    state = core.load_state(zone)
    title('VÉRIFICATION AUTORITAIRE · ' + zone)
    if not shutil.which('dig'):
        raise DNSError('dig absent. Exécuter autokom dns install.')
    targets = [('local', '127.0.0.1'), ('primaire', state['primary_ip'])]
    if state.get('secondary_ip'):
        targets.append(('secondaire', state['secondary_ip']))
    else:
        info('Secondaire : non configuré — aucune requête envoyée.')
    for place, addr in targets:
        p = run(['dig', '+norecurse', '+time=3', '+tries=1', '@'+addr, zone, 'SOA', '+noall', '+answer'], timeout=9)
        values = p.stdout.strip()
        print(f'  {place:<8} {addr:<16} ' + (values[:180] if values else 'AUCUNE RÉPONSE'))
    info('Vérifier SOA/NS et port 53 UDP/TCP depuis un réseau extérieur avant délégation.')
    return 0


def tsig(args):
    zone = core.validate_zone(args.zone)
    path = core.tsig_path(zone)
    if args.action == 'status':
        title('TSIG · ' + zone)
        info(f'Clé locale : {"présente" if path.is_file() else "absente"} ({path})')
        return 0
    if args.action == 'generate':
        core.need_root()
        if path.exists():
            good('Clé déjà présente, aucune régénération.'); return 0
        if not confirmation('Créer une clé de transfert TSIG ?', expected='CRÉER', yes=args.yes): return 0
        if _ensure_tsig_primary(zone): good('Clé créée en dehors de Git.')
        return 0
    raise DNSError('Action TSIG inconnue.')


def cloudflare(args):
    from autokom_dns_cloudflare import Cloudflare
    if args.action == 'delegation':
        title('DÉLÉGATION CLOUDFLARE')
        info('ikom.mg : deux NS Cloudflare uniquement chez dot.mg.')
        info('ns1.ikom.mg doit pointer sur l’IP publique actuelle du primaire BIND (DNS only).')
        zone_path = core.state_path('infra.ikom.mg')
        secondary_ready = zone_path.is_file() and core.load_state('infra.ikom.mg').get('secondary_ip')
        if not secondary_ready:
            info('Primaire seul : disponibilité DNS non redondante ; vérifier la délégation avant arrêt de l’ancien.')
            info('Prévoir un DNS secondaire indépendant et une IP publique statique.')
        else:
            info('ns2.ikom.mg A = IP publique du secondaire (DNS only, non proxifié)')
            info('infra.ikom.mg NS = ns1.ikom.mg et ns2.ikom.mg (après tests GCP)')
        return 0
    cf = Cloudflare()
    if args.action == 'list':
        title('CLOUDFLARE · ZONE PUBLIQUE')
        for item in cf.records():
            value = str(item.get('content', ''))[:120]
            print(f'  {item["type"]:<6} {item["name"]:<39} {value:<55} ID={item["id"]}')
        return 0
    if args.action == 'add':
        if not args.apply:
            info(f'Simulation : création {args.type} {args.name} dans {args.zone}. --apply pour confirmer.')
            return 0
        core.need_root()
        if not confirmation('Créer un enregistrement DNS Cloudflare ?', expected='CRÉER', yes=args.yes): return 0
        obj = cf.create(args.zone, args.type, args.name, args.value, args.ttl, args.priority, args.proxied)
        good(('Déjà présent : ' if obj.get('_autokom_unchanged') else 'Créé : ') + obj['type'] + ' ' + obj['name'])
        return 0
    if args.action == 'delete':
        if not args.apply:
            info('Simulation : suppression Cloudflare (ID requis). --apply pour confirmer.')
            return 0
        core.need_root()
        if not confirmation('Supprimer cet enregistrement Cloudflare ?', expected='SUPPRIMER', yes=args.yes): return 0
        cf.delete(args.id)
        good('Enregistrement supprimé.'); return 0
    raise DNSError('Action Cloudflare inconnue.')


def menu():
    options = ['Installer BIND9', 'Configurer une zone primaire ou secondaire',
               'Lister les zones', 'Lister les environnements', 'État et diagnostic',
               'Vérifier une zone', 'Voir la délégation Cloudflare', 'Quitter']
    title('GESTION DES ZONES')
    i = choose('Gestion DNS AUTOKOM', options)
    if not i or i == 8: return 0
    if i == 1: return install(argparse.Namespace(check=False, apply=False, yes=False))
    if i == 2:
        return bind_setup(argparse.Namespace(role=None, zone=None, environment=None,
                          primary_ip=None, secondary_ip=None, ns1=None, ns2=None,
                          listen_ip=None, check=False, apply=True, yes=False))
    if i == 3: return list_zones()
    if i == 4: return list_zones(only_environments=True)
    if i == 5: return status()
    if i == 6: return verify(argparse.Namespace(zone=ask('Zone à vérifier', 'infra.ikom.mg')))
    if i == 7: return cloudflare(argparse.Namespace(action='delegation'))
    return 0


def pick_zone():
    zones=[]
    path=core.STATE_ROOT/'zones'
    if path.is_dir():
        zones=sorted(p.name[:-5] for p in path.glob('*.json'))
    if not zones:raise DNSError('Aucune zone configurée : autokom dns setup.')
    if len(zones)==1:return zones[0]
    idx=choose('Sélectionner une zone',zones)
    if not idx:raise DNSError('Annulé.')
    return zones[idx-1]


def record_menu():
    choice=choose('Enregistrements DNS',[
        'Lister les enregistrements','Ajouter un enregistrement','Supprimer un enregistrement','Annuler'])
    if choice in (0,4):return 0
    zone=pick_zone()
    if choice==1:return bind_record(argparse.Namespace(action='list',zone=zone))
    if choice==2:
        kind_options=['A','AAAA','CNAME','MX','TXT']
        index=choose('Type DNS',kind_options)
        if not index:return 0
        kind=kind_options[index-1]
        return bind_record(argparse.Namespace(action='add',zone=zone,type=kind,
            name=ask('Nom relatif à la zone', '@'),value=ask('Valeur DNS'),
            ttl=300,priority=int(ask('Priorité MX','10')) if kind=='MX' else None,
            check=False,apply=True,yes=False))
    return bind_record(argparse.Namespace(action='delete',zone=zone,
        index=int(ask('Index à supprimer')),check=False,apply=True,yes=False))


def tsig_menu():
    index=choose('Clé de transfert TSIG',['État de la clé','Générer si absente','Annuler'])
    if index in (0,3):return 0
    return tsig(argparse.Namespace(action='status' if index==1 else 'generate',
                                    zone=pick_zone(),yes=False))


def cloudflare_menu():
    index=choose('Cloudflare', ['Enregistrements','Délégation NS',
                                'Configuration API / secrets','Annuler'])
    if index in (0,4):return 0
    if index==3:
        from autokom_env_manager import main as env_main
        return env_main('cloudflare',[])
    return cloudflare(argparse.Namespace(action='list' if index==1 else 'delegation'))


def parse(argv=None):
    p = argparse.ArgumentParser(prog='autokom dns', description='Gestion DNS Cloudflare et BIND9 AUTOKOM')
    sub = p.add_subparsers(dest='cmd')
    for item in ('install', 'setup'):
        c = sub.add_parser(item)
        c.add_argument('--check', action='store_true', help='Afficher le plan sans modification')
        c.add_argument('--apply', action='store_true', help='Autoriser une modification confirmée')
        c.add_argument('--yes', action='store_true', help='Confirmation Ansible non interactive')
        if item == 'setup':
            c.add_argument('--role', choices=('primary','secondary'))
            c.add_argument('--zone')
            c.add_argument('--environment', choices=('production','staging','development'))
            c.add_argument('--primary-ip')
            c.add_argument('--secondary-ip', help='IPv4 publique de l’éventuel serveur secondaire')
            c.add_argument('--ns1', default='ns1.ikom.mg')
            c.add_argument('--ns2', default='ns2.ikom.mg')
            c.add_argument('--listen-ip', help='IPv4 locale de l’interface VM, sous NAT GCP/AWS')
    mig = sub.add_parser('migrate', help='Exporter AWS et promouvoir la zone AUTOKOM sur GCP')
    ms = mig.add_subparsers(dest='action')
    exp = ms.add_parser('export', help='Créer sur AWS une archive privée des zones BIND AUTOKOM')
    exp.add_argument('--zone', default='infra.ikom.mg')
    exp.add_argument('--archive', required=True)
    imp = ms.add_parser('import', help='Restaurer sans écraser une zone existante sur GCP')
    imp.add_argument('--zone', default='infra.ikom.mg')
    imp.add_argument('--archive', required=True)
    imp.add_argument('--public-ip', default=None)
    imp.add_argument('--listen-ip', default=None)
    imp.add_argument('--apply', action='store_true')
    imp.add_argument('--yes', action='store_true')
    sub.add_parser('status')
    sub.add_parser('list')
    sub.add_parser('environments')
    v = sub.add_parser('verify'); v.add_argument('--zone', default='infra.ikom.mg')
    rec = sub.add_parser('record'); r = rec.add_subparsers(dest='action')
    l = r.add_parser('list'); l.add_argument('--zone')
    a = r.add_parser('add'); a.add_argument('--zone', required=True)
    a.add_argument('--type', required=True); a.add_argument('--name', required=True); a.add_argument('--value', required=True)
    a.add_argument('--ttl', type=int, default=300); a.add_argument('--priority', type=int)
    d = r.add_parser('delete'); d.add_argument('--zone', required=True); d.add_argument('--index', type=int, required=True)
    for sp in (a,d):
        sp.add_argument('--check', action='store_true'); sp.add_argument('--apply', action='store_true'); sp.add_argument('--yes', action='store_true')
    t = sub.add_parser('tsig'); ts = t.add_subparsers(dest='action')
    for action in ('status','generate'):
        x = ts.add_parser(action); x.add_argument('--zone')
        if action == 'generate': x.add_argument('--yes', action='store_true')
    cl = sub.add_parser('cloudflare'); ca = cl.add_subparsers(dest='action')
    env = ca.add_parser('env', help='Configurer Cloudflare sans fichier exemple',add_help=False);env.add_argument('env_args',nargs=argparse.REMAINDER)
    ca.add_parser('list'); ca.add_parser('delegation')
    cr = ca.add_parser('add'); cr.add_argument('--zone', default='ikom.mg'); cr.add_argument('--name', required=True)
    cr.add_argument('--type', required=True); cr.add_argument('--value', required=True)
    cr.add_argument('--ttl', type=int, default=300); cr.add_argument('--priority', type=int)
    cr.add_argument('--proxied', action='store_true')
    cd = ca.add_parser('delete'); cd.add_argument('--id', required=True)
    for cp in (cr,cd): cp.add_argument('--apply', action='store_true'); cp.add_argument('--yes', action='store_true')
    return p.parse_args(argv)


def main(argv=None):
    raw=list(sys.argv[1:] if argv is None else argv)
    if raw[:2]==['cloudflare','env']:
        from autokom_env_manager import main as env_main, EnvError
        try:return env_main('cloudflare',raw[2:])
        except EnvError as exc:raise DNSError(str(exc)) from exc
    args = parse(argv)
    try:
        if args.cmd is None: return menu()
        if args.cmd == 'migrate':
            from autokom_dns_migrate import export_zone, import_zone
            if args.action == 'export':
                export_zone(args.zone, args.archive)
                return 0
            if args.action == 'import':
                from domain_validation import instance_public_ipv4
                provider, detected = instance_public_ipv4()
                addr = args.public_ip or detected
                if not addr:
                    raise DNSError('IP publique de ce serveur introuvable (GCP/AWS metadata) : fournir --public-ip.')
                interfaces = local_listen_ips()
                listen = args.listen_ip or route_listen_ip(interfaces)
                if not listen:
                    raise DNSError('Plusieurs interfaces : fournir --listen-ip avec l’IPv4 locale GCP.')
                authorized = False
                if args.apply:
                    authorized = confirmation('Promouvoir sur ce serveur la zone DNS importée ?', expected='MIGRER', yes=args.yes)
                    if not authorized:
                        raise DNSError('Migration DNS non confirmée ; aucun changement.')
                import_zone(zone=args.zone, archive=args.archive, public_ip=addr,
                            listen_ip=listen, apply=args.apply, confirmed=authorized)
                return 0
            raise DNSError('Utiliser autokom dns migrate export ou import.')
        if args.cmd == 'install': return install(args)
        if args.cmd == 'setup': return bind_setup(args)
        if args.cmd == 'status': return status()
        if args.cmd == 'list': return list_zones()
        if args.cmd == 'environments': return list_zones(True)
        if args.cmd == 'verify': return verify(args)
        if args.cmd == 'record':
            if not args.action: return record_menu()
            if not getattr(args,'zone',None):
                args.zone = pick_zone()
            return bind_record(args)
        if args.cmd == 'tsig':
            if not args.action: return tsig_menu()
            if not getattr(args,'zone',None):args.zone=pick_zone()
            return tsig(args)
        if args.cmd == 'cloudflare':
            if not args.action: return cloudflare_menu()
            if args.action == 'env':
                from autokom_env_manager import main as env_main, EnvError
                try: return env_main('cloudflare',args.env_args)
                except EnvError as exc: raise DNSError(str(exc)) from exc
            return cloudflare(args)
        raise DNSError('Commande DNS inconnue.')
    except KeyboardInterrupt:
        print('\n  ! Opération annulée.')
        return 130


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except DNSError as exc:
        print('  ✗ ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
