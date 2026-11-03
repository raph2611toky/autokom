#!/usr/bin/env python3
"""Safe, testable authoritative BIND9 zone data for AUTOKOM.

Writes only AUTOKOM-managed paths. DNS state and secrets live off-repository.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CONF_ROOT = Path('/etc/bind/autokom')
STATE_ROOT = Path('/var/lib/autokom/dns')
BIND_ROOT = Path('/etc/bind')
ZONE_ROOT = Path('/var/lib/bind/autokom')
MANAGED_INCLUDE = BIND_ROOT / 'named.conf.autokom'
LOCAL_INCLUDE = BIND_ROOT / 'named.conf.local'

class DNSError(RuntimeError):
    pass


def validate_name(name: str) -> str:
    s = (name or '').lower().strip().rstrip('.')
    if len(s) > 253 or not s or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', s):
        raise DNSError('Nom DNS invalide (nom de domaine complet requis).')
    if any(len(part) > 63 or part.startswith('-') or part.endswith('-') for part in s.split('.')):
        raise DNSError('Nom DNS invalide (étiquette DNS).')
    return s


def validate_ip(value: str, family: int | None = None) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise DNSError('Adresse IP invalide.') from exc
    if family and ip.version != family:
        raise DNSError('Famille IP incorrecte.')
    return str(ip)


def validate_zone(zone: str) -> str:
    zone = validate_name(zone)
    if zone == 'ikom.mg':
        raise DNSError('La zone racine ikom.mg est gérée sur Cloudflare. Déléguer une sous-zone, ex. infra.ikom.mg.')
    if not zone.endswith('.ikom.mg'):
        raise DNSError('Cette première version gère uniquement une sous-zone déléguée de ikom.mg.')
    return zone


def state_path(zone: str) -> Path:
    return STATE_ROOT / 'zones' / (validate_zone(zone) + '.json')


def bind_path(zone: str) -> Path:
    return ZONE_ROOT / ('db.' + validate_zone(zone))


def tsig_path(zone: str) -> Path:
    return CONF_ROOT / 'keys' / (validate_zone(zone) + '.key')


def managed_zone_conf(zone: str) -> Path:
    return CONF_ROOT / 'zones' / (validate_zone(zone) + '.conf')


def atomic_write(path: Path, content: str, mode=0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.autokom-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_state(zone: str) -> dict:
    path = state_path(zone)
    if not path.is_file():
        raise DNSError(f'Zone non configurée dans AUTOKOM : {zone}')
    return json.loads(path.read_text(encoding='utf-8'))


def fqdn(name: str, zone: str) -> str:
    if name in ('@', ''):
        return validate_zone(zone) + '.'
    if name.endswith('.'):
        value = name[:-1]
    elif '.' in name:
        value = name
    else:
        value = name + '.' + zone
    if not re.fullmatch(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)+', value) or len(value) > 253:
        raise DNSError('Nom de propriétaire DNS invalide.')
    if not (value == zone or value.endswith('.' + zone)):
        raise DNSError('Enregistrement en dehors de la zone DNS.')
    return value + '.'


def destination_name(value: str) -> str:
    return validate_name(value) + '.'


def normalize_record(zone: str, kind: str, name: str, value: str, ttl: int = 300,
                     priority: int | None = None) -> dict:
    zone = validate_zone(zone)
    kind = kind.upper()
    if kind not in ('A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS'):
        raise DNSError('Types locaux pris en charge : A, AAAA, CNAME, MX, TXT, NS.')
    owner = fqdn(name, zone)
    if not 60 <= int(ttl) <= 86400:
        raise DNSError('TTL accepté : 60 à 86400 secondes.')
    value = value.strip()
    if kind == 'A':
        value = validate_ip(value, 4)
    elif kind == 'AAAA':
        value = validate_ip(value, 6)
    elif kind in ('CNAME', 'MX', 'NS'):
        value = destination_name(value)
    else:
        if not value or len(value.encode('utf-8')) > 255 or any(ord(ch) < 32 for ch in value):
            raise DNSError('TXT : valeur vide, trop longue ou contenant un caractère de contrôle.')
    if kind == 'MX':
        if priority is None or not 0 <= int(priority) <= 65535:
            raise DNSError('Un enregistrement MX exige --priority entre 0 et 65535.')
    else:
        if priority is not None:
            raise DNSError('Priorité réservée aux enregistrements MX.')
    return dict(type=kind, name=owner, value=value, ttl=int(ttl),
                **({'priority': int(priority)} if kind == 'MX' else {}))


def bind_quote(value: str) -> str:
    # normalize_record rejects control chars and enforces a single TXT segment
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def zone_text(state: dict) -> str:
    zone = validate_zone(state['zone'])
    ns1, ns2 = destination_name(state['ns1']), destination_name(state['ns2'])
    serial = int(state['serial'])
    lines = [f'; AUTOKOM-DNS managed zone: {zone}. Do not edit directly.', '$ORIGIN ' + zone + '.',
             '$TTL 300',
             f'@ IN SOA {ns1} hostmaster.{zone}. ( {serial} 3600 900 1209600 300 )',
             f'@ IN NS {ns1}', f'@ IN NS {ns2}']
    for record in state.get('records', []):
        owner = record['name']
        kind = record['type']
        val = record['value']
        if kind == 'TXT': val = bind_quote(val)
        elif kind == 'MX': val = f"{int(record['priority'])} {val}"
        lines.append(f"{owner} {int(record['ttl'])} IN {kind} {val}")
    return '\n'.join(lines) + '\n'


def bump_serial(state: dict) -> None:
    # Monotonically increasing across rapid changes and clock adjustments.
    candidate = int(datetime.now(timezone.utc).strftime('%Y%m%d')) * 100
    state['serial'] = max(candidate, int(state.get('serial', 0)) + 1)


def check_zone(zone: str, content: str) -> None:
    binary = shutil.which('named-checkzone')
    if not binary:
        raise DNSError('named-checkzone absent : installer BIND9 avant la modification.')
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False,
                                     prefix='autokom-zone-', suffix='.dns') as temp:
        temp.write(content)
        name = temp.name
    try:
        p = subprocess.run([binary, zone, name], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    finally:
        Path(name).unlink(missing_ok=True)
    if p.returncode:
        raise DNSError('Zone rejetée par named-checkzone : ' + (p.stderr or p.stdout)[-650:])


def add_record(state: dict, record: dict) -> dict:
    records = state['records']
    same = [r for r in records if r['name'] == record['name']]
    if record['type'] == 'CNAME' and same:
        raise DNSError('CNAME incompatible avec un autre enregistrement au même nom.')
    if any(r['type'] == 'CNAME' for r in same):
        raise DNSError('Un CNAME existe déjà à ce nom.')
    if any(r == record for r in records):
        raise DNSError('Enregistrement déjà présent.')
    records.append(record)
    bump_serial(state)
    return state


def delete_record(state: dict, index: int) -> dict:
    records = state['records']
    if index < 1 or index > len(records):
        raise DNSError('Numéro d’enregistrement invalide.')
    del records[index - 1]
    bump_serial(state)
    return state


def render_bind_zone_statement(state: dict) -> str:
    zone = validate_zone(state['zone'])
    role = state['role']
    key = tsig_path(zone)
    if role not in ('primary', 'secondary'):
        raise DNSError('Rôle inconnu.')
    # All strings entering BIND are validated domain names and IP addresses.
    prefix = f'// AUTOKOM-DNS {zone}: managed, do not edit\n'
    prefix += f'include "{key}";\n'
    if role == 'primary':
        return (prefix + f'zone "{zone}" {{\n type primary;\n file "{bind_path(zone)}";\n'
                f' allow-transfer {{ key "autokom-{zone}"; }};\n'
                f' also-notify {{ {validate_ip(state["secondary_ip"], 4)} key "autokom-{zone}"; }};\n'
                ' notify yes;\n};\n')
    return (prefix + f'zone "{zone}" {{\n type secondary;\n'
            f' primaries {{ {validate_ip(state["primary_ip"], 4)} key "autokom-{zone}"; }};\n'
            f' file "/var/cache/bind/autokom/db.{zone}";\n'
            ' allow-transfer { none; };\n};\n')


def ensure_includes(zone: str) -> tuple[str, str]:
    """Return managed include and main local include; never remove third-party text."""
    zone = validate_zone(zone)
    frag = f'include "{managed_zone_conf(zone)}";'
    managed = MANAGED_INCLUDE.read_text() if MANAGED_INCLUDE.exists() else '// AUTOKOM-DNS managed zone includes\n'
    if frag not in managed:
        managed += frag + '\n'
    local = LOCAL_INCLUDE.read_text() if LOCAL_INCLUDE.exists() else ''
    line = f'include "{MANAGED_INCLUDE}";'
    if line not in local:
        local = local.rstrip() + '\n\n// AUTOKOM-DNS managed include\n' + line + '\n'
    return managed, local


def verify_named_conf() -> None:
    binary = shutil.which('named-checkconf')
    if not binary:
        raise DNSError('named-checkconf absent.')
    p = subprocess.run([binary, '-z'], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise DNSError('Configuration BIND invalide : ' + (p.stderr or p.stdout)[-850:])


def need_root() -> None:
    if os.geteuid() != 0:
        raise DNSError('Utiliser sudo autokom dns ... pour modifier la configuration DNS.')
