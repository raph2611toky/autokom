"""Dedicated AUTOKOM preview tunnel. No changes to existing WireGuard interfaces."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile

ROOT = Path('/etc/autokom-preview')
REGISTRY = ROOT / 'peers.json'
SYNC = ROOT / 'peers.conf'
SERVER = '10.203.53.1'
POOL = ipaddress.IPv4Network('10.203.0.0/16')
PORT = 51896
HTTPS_PORT = 18443
DEVICE = 'akpreview'


class WireGuardError(RuntimeError):
    pass


def atomic(path, text, mode=0o600):
    fd, tmp = tempfile.mkstemp(prefix='.autokom-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read(path, limit=524288):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_size > limit):
        raise WireGuardError('État WireGuard non conforme.')
    return path.read_text(encoding='ascii')


def key(value):
    try:
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9+/]{43}=', value):
            binary = base64.b64decode(value, validate=True)
            if len(binary) == 32 and any(binary) and base64.b64encode(binary).decode() == value:
                return value
    except ValueError:
        pass
    raise WireGuardError('Clé WireGuard non conforme.')


def endpoint(value):
    try:
        if ipaddress.IPv4Address(value).is_global:
            return value
    except (ValueError, TypeError):
        pass
    raise WireGuardError('Adresse IPv4 publique WireGuard absente.')


def registry():
    if not REGISTRY.exists() and not REGISTRY.is_symlink():
        return {}
    rows = json.loads(read(REGISTRY))
    if not isinstance(rows, dict) or len(rows) > 2048:
        raise WireGuardError('Registre WireGuard non conforme.')
    addresses, keys = set(), set()
    for scope, row in rows.items():
        if not re.fullmatch(r'(?:preprod|main)-[a-f0-9-]{36}', scope) or not isinstance(row, dict):
            raise WireGuardError('Registre WireGuard non conforme.')
        addr = ipaddress.IPv4Address(row['address'])
        if addr not in POOL or addr in (POOL.network_address, POOL.broadcast_address, ipaddress.IPv4Address(SERVER)) or addr in addresses:
            raise WireGuardError('Adresse WireGuard non conforme.')
        addresses.add(addr)
        if row.get('public_key'):
            public = key(row['public_key'])
            if public in keys:
                raise WireGuardError('Clé WireGuard déjà présente.')
            keys.add(public)
    return rows


def scope(environment, preprod):
    return ('preprod-' if preprod else 'main-') + environment


def plan(rows, identity):
    if identity in rows:
        return rows[identity]['address']
    if len(rows) >= 2048:
        raise WireGuardError('Capacité WireGuard atteinte.')
    taken = {r['address'] for r in rows.values()} | {SERVER}
    seed = int(hashlib.sha256(identity.encode('ascii')).hexdigest()[:8], 16)
    for i in range(65534):
        addr = str(POOL.network_address + (seed + i) % 65534 + 1)
        if addr not in taken:
            return addr
    raise WireGuardError('Capacité WireGuard atteinte.')


def sync_config(rows):
    server_config = read(ROOT / (DEVICE + '.conf'), 1024)
    private_keys = [line.split('=', 1)[1].strip() for line in server_config.splitlines()
                    if line.startswith('PrivateKey = ')]
    if len(private_keys) != 1:
        raise WireGuardError('Configuration serveur WireGuard non conforme.')
    blocks = ['[Interface]', 'PrivateKey = ' + key(private_keys[0]), f'ListenPort = {PORT}']
    for row in rows.values():
        if row.get('public_key'):
            blocks += ['', '[Peer]', 'PublicKey = ' + key(row['public_key']),
                       'AllowedIPs = ' + str(ipaddress.IPv4Address(row['address'])) + '/32']
    return '\n'.join(blocks) + '\n'


def persist(old, new, execute):
    # Stage the kernel configuration, then commit registry. On failure restore
    # the original allowed peers. No client private key is persisted.
    atomic(SYNC, sync_config(new))
    try:
        execute(['systemctl', 'start', 'autokom-preview-peers.service'], timeout=10)
        atomic(REGISTRY, json.dumps(new, sort_keys=True) + '\n')
    except Exception:
        atomic(SYNC, sync_config(old))
        execute(['systemctl', 'start', 'autokom-preview-peers.service'], timeout=10)
        raise


def ready(execute, public_ip):
    public_ip = endpoint(public_ip)
    execute(['systemctl', 'is-active', '--quiet', 'autokom-preview-vpn.service'], timeout=3)
    execute(['systemctl', 'is-active', '--quiet', 'autokom-preview-dns.service'], timeout=3)
    return public_ip, key(read(ROOT / 'server.pub', 128).strip())


def issue(rows, identity, execute, server_endpoint, server_key):
    private = key(execute(['wg', 'genkey'], timeout=3).strip())
    public = key(execute(['wg', 'pubkey'], input=private + '\n', timeout=3).strip())
    address = rows[identity]['address']
    persist(rows, {**rows, identity: {'address': address, 'public_key': public}}, execute)
    return (f'[Interface]\nPrivateKey = {private}\nAddress = {address}/32\nDNS = {SERVER}\n'
            f'\n[Peer]\nPublicKey = {server_key}\nEndpoint = {server_endpoint}:{PORT}\n'
            f'AllowedIPs = {SERVER}/32\nPersistentKeepalive = 25\n')
