#!/usr/bin/env python3
"""Confinement idempotent du listener Jenkins, sans toucher aux vhosts Nginx.

Ne modifie rien durant inspection. Toute interruption Jenkins existante exige
une confirmation explicite. Restaure son drop-in systemd en cas d'échec.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import tempfile
import time

DROPIN = Path('/etc/systemd/system/jenkins.service.d/20-autokom-loopback.conf')
MARKER = '# Managed by AUTOKOM: phase-2'


class NetworkError(RuntimeError):
    pass


def call(*cmd: str, timeout: int = 40):
    try:
        p = subprocess.run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError(f'Commande non exécutable : {cmd[0]}') from exc
    if p.returncode:
        raise NetworkError(f'{cmd[0]} échoue (code {p.returncode}) : {(p.stderr or p.stdout)[-500:]}')
    return p.stdout


def bindings(port: int):
    lines = call('ss', '-H', '-lnt').splitlines()
    hosts = []
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        address = parts[3]
        if address.rsplit(':', 1)[-1] == str(port):
            hosts.append(address)
    return hosts


def private_listener(hosts):
    if not hosts:
        return False
    return all(x.rsplit(':', 1)[0].strip('[]') in ('127.0.0.1', '::1') for x in hosts)


def _write_atomic(path: Path, data: str):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(path.parent), prefix='.autokom-dropin-')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def enforce(port: int, *, approved: bool = False, timeout: int = 90,
            dropin: Path = DROPIN, read_bindings=bindings, executor=call):
    """Retours: unchanged / fixed. Ne réécrit pas les overrides tiers."""
    actual = read_bindings(port)
    if private_listener(actual):
        return 'unchanged'
    if not actual:
        raise NetworkError(f'Jenkins ne semble pas écouter sur TCP/{port} : refuser le redémarrage automatique')
    if not approved:
        raise NetworkError(f'Jenkins TCP/{port} exposé : confirmation interactive requise')
    original = dropin.read_text(encoding='utf-8') if dropin.exists() else None
    if original is not None and MARKER not in original:
        raise NetworkError('Override Jenkins existant hors AUTOKOM : aucune écriture')
    desired = MARKER + '\n[Service]\nEnvironment="JENKINS_LISTEN_ADDRESS=127.0.0.1"\n'
    if original != desired:
        _write_atomic(dropin, desired)
    try:
        executor('systemctl', 'daemon-reload')
        executor('systemctl', 'restart', 'jenkins', timeout=90)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if private_listener(read_bindings(port)):
                executor('systemctl', 'is-active', '--quiet', 'jenkins')
                return 'fixed'
            time.sleep(2)
        raise NetworkError('Jenkins ne revient pas en écoute privée : restauration de son override')
    except Exception as error:
        # Restore only OUR file, never any other systemd configuration.
        if original is None:
            dropin.unlink(missing_ok=True)
        else:
            _write_atomic(dropin, original)
        try:
            executor('systemctl', 'daemon-reload')
            executor('systemctl', 'restart', 'jenkins', timeout=90)
        except Exception as rollback_error:
            raise NetworkError(f'Restauration Jenkins incomplète : {rollback_error}') from error
        raise
