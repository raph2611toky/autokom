#!/usr/bin/env python3
"""Apply AUTOKOM HA worker code atomically on a *single* worker VM.

This file is transported by the authenticated control node, never fetched from
an untrusted network.  It executes locally as root on the target worker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

FILES = {
    'agent': Path('/usr/local/lib/autokom-ha/worker-agent.py'),
    'health': Path('/usr/local/lib/autokom-ha/worker-health.py'),
}
SERVICES = ('autokom-ha-worker-agent.service', 'autokom-ha-worker-health.service')
BACKUP_ROOT = Path('/var/lib/autokom-ha/code-backups')


class WorkerSyncError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for part in iter(lambda: stream.read(131072), b''):
            digest.update(part)
    return digest.hexdigest()


def run(*argv, timeout=30):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WorkerSyncError('Command unavailable or timed out: ' + argv[0]) from e
    if p.returncode:
        raise WorkerSyncError('Command failed: ' + ' '.join(argv[:3]) + ': ' + (p.stderr or '')[-300:])
    return p.stdout.strip()


def replace_from(source, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        raise WorkerSyncError('Refusing symlink at destination ' + str(dest))
    fd, name = tempfile.mkstemp(prefix='.autokom-ha-', dir=dest.parent)
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o644)
        os.replace(name, dest)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_payload(files, digests):
    for kind, src in files.items():
        if src.is_symlink() or not src.is_file():
            raise WorkerSyncError('Missing or symbolic payload: ' + kind)
        if sha256(src) != digests[kind]:
            raise WorkerSyncError('SHA256 mismatch: ' + kind)
        # Syntax check in memory: no __pycache__ created on /run.
        compile(src.read_bytes(), str(src), 'exec')


def apply(files, digests):
    if os.geteuid() != 0:
        raise WorkerSyncError('Worker code sync must run as root')
    validate_payload(files, digests)
    before = {kind: sha256(dst) if dst.is_file() and not dst.is_symlink() else None
              for kind, dst in FILES.items()}
    changed = [kind for kind in FILES if before[kind] != digests[kind]]
    if not changed:
        for service in SERVICES:
            run('systemctl', 'is-active', '--quiet', service)
        return {'result': 'up-to-date', 'changed': [], 'sha256': before}
    for kind, dst in FILES.items():
        if not dst.is_file() or dst.is_symlink():
            raise WorkerSyncError('Missing or unsafe installed source: ' + kind)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_ROOT, 0o700)
    # Ensure unique backups across restarts and repeated attempts.
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid())
    backup = BACKUP_ROOT / stamp
    backup.mkdir(mode=0o700)
    for kind, original in FILES.items():
        shutil.copy2(original, backup / (kind + '.py'))
    agent_stopped = False
    try:
        run('systemctl', 'stop', SERVICES[0])
        agent_stopped = True
        for kind in changed:
            replace_from(files[kind], FILES[kind])
        run('systemctl', 'restart', SERVICES[1])
        run('systemctl', 'start', SERVICES[0])
        agent_stopped = False
        for service in SERVICES:
            run('systemctl', 'is-active', '--quiet', service)
        return {'result': 'updated', 'changed': changed, 'backup': str(backup),
                'sha256': {k: sha256(v) for k, v in FILES.items()}}
    except (WorkerSyncError, OSError) as exc:
        # Never leave worker-agent disabled or half-updated on a failed deploy.
        for kind in FILES:
            replace_from(backup / (kind + '.py'), FILES[kind])
        for service in (SERVICES[1], SERVICES[0]):
            subprocess.run(('systemctl', 'restart', service), capture_output=True, timeout=25)
        raise WorkerSyncError('Worker code update failed; previous sources restored: ' + str(exc)) from exc
    finally:
        if agent_stopped:
            subprocess.run(('systemctl', 'start', SERVICES[0]), capture_output=True, timeout=25)


def main(argv=None):
    p = argparse.ArgumentParser(description='AUTOKOM HA single worker code apply (privileged)')
    p.add_argument('--agent', type=Path, required=True)
    p.add_argument('--health', type=Path, required=True)
    p.add_argument('--sha-agent', required=True)
    p.add_argument('--sha-health', required=True)
    args = p.parse_args(argv)
    try:
        digests = {'agent': args.sha_agent, 'health': args.sha_health}
        if any(len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in digests.values()):
            raise WorkerSyncError('Bad SHA256 specification')
        print(json.dumps(apply({'agent': args.agent, 'health': args.health}, digests), sort_keys=True))
        return 0
    except (WorkerSyncError, OSError, SyntaxError) as exc:
        print('AUTOKOM_HA_WORKER_SYNC_ERROR ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
