#!/usr/bin/env python3
"""AUTOKOM HA worker code sync: MIG discovery, sequential safe updates, timer.

One-time activation on the main GCP controller enables automatic reconciliation
for existing *and replacement* MIG instances.  Uses a dedicated, root-owned SSH
key for noninteractive gcloud compute ssh/scp.  No public IP or IAP is needed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'terraform/modules/gcp_regional_ha/files'
REMOTE_APPLIER = ROOT / 'scripts/lib/autokom_ha_worker_remote_apply.py'
PROJECT = 'plucky-stacker-480421-c4'
REGION = 'us-central1'
MIG = 'autokom-ha-rmig'
NAME = re.compile(r'autokom-ha-web-[a-z0-9-]{3,25}\Z')
ZONE = re.compile(r'us-central1-[a-z]\Z')
WORK_DIR = Path('/var/lib/autokom/ha-worker-sync')
KEY = WORK_DIR / 'worker_sync_ed25519'
LOCK = Path('/run/lock/autokom-ha-worker-sync.lock')
UNIT_DIR = Path('/etc/systemd/system')
SERVICE_NAME = 'autokom-ha-worker-sync.service'
TIMER_NAME = 'autokom-ha-worker-sync.timer'
SOURCE_MAP = {
    'agent': SOURCES / 'worker-agent.py',
    'health': SOURCES / 'worker-delivery-health.py',
    'apply': REMOTE_APPLIER,
}


class SyncError(RuntimeError):
    pass


def command(args, timeout=90):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, 'CLOUDSDK_CORE_DISABLE_PROMPTS': '1'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError('Unable to execute or timed out: ' + args[0]) from exc
    if p.returncode:
        detail = (p.stderr or p.stdout or '')[-600:].strip()
        # Do not echo CLI argv; these have no secrets, but may contain URI and paths.
        raise SyncError(f'{args[0]} returned {p.returncode}: {detail}')
    return p.stdout


def checked_files():
    result = {}
    for kind, path in SOURCE_MAP.items():
        if path.is_symlink() or not path.is_file():
            raise SyncError('Missing or symlink source: ' + kind)
        content = path.read_bytes()
        compile(content, str(path), 'exec')
        result[kind] = hashlib.sha256(content).hexdigest()
    return result


def discover():
    raw = command([
        'gcloud', 'compute', 'instance-groups', 'managed', 'list-instances', MIG,
        '--region=' + REGION, '--project=' + PROJECT, '--format=json',
    ], timeout=35)
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SyncError('Invalid GCP MIG inventory') from exc
    if not isinstance(rows, list) or not rows:
        raise SyncError('No MIG members found; refusing synchronization')
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise SyncError('Unexpected MIG inventory row')
        resource = str(row.get('instance', ''))
        parts = resource.split('/')
        if len(parts) < 5 or parts[-4] != 'zones' or parts[-2] != 'instances':
            raise SyncError('Unrecognized MIG instance resource')
        zone, name = parts[-3], parts[-1]
        if not NAME.fullmatch(name) or not ZONE.fullmatch(zone):
            raise SyncError('Unexpected MIG member identity: ' + name)
        status = row.get('instanceStatus') or row.get('status') or ''
        action = row.get('currentAction') or 'NONE'
        out.append({'name': name, 'zone': zone, 'status': status, 'action': action})
    if len(out) > 20:
        raise SyncError('Unexpected MIG size; refusing broad rollout')
    return sorted(out, key=lambda x: (x['zone'], x['name']))


def prepare_key():
    if os.geteuid() != 0:
        raise SyncError('Worker synchronization requires root on the main controller')
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if WORK_DIR.is_symlink():
        raise SyncError('Unsafe work directory symlink')
    os.chmod(WORK_DIR, 0o700)
    if KEY.is_symlink():
        raise SyncError('SSH key may not be a symlink')
    if not KEY.exists():
        # Deliberate dedicated, root-only key for unattended operation.  No GH token.
        command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
                 'autokom-ha-worker-sync', '-f', str(KEY)], timeout=20)
    os.chmod(KEY, 0o600)
    if not KEY.with_suffix('.pub').exists() and not Path(str(KEY) + '.pub').exists():
        raise SyncError('SSH public key missing')


def gcloud_flags(member):
    return [
        '--project=' + PROJECT, '--zone=' + member['zone'], '--internal-ip',
        '--ssh-key-file=' + str(KEY), '--ssh-key-expire-after=30m', '--quiet',
    ]


def remote_ssh(member, remote, timeout=90):
    return command(['gcloud', 'compute', 'ssh', 'root@' + member['name'],
                    *gcloud_flags(member), '--command=' + remote,
                    '--ssh-flag=-oBatchMode=yes', '--ssh-flag=-oConnectTimeout=8'], timeout=timeout)


def remote_scp(member, local, dest, timeout=90):
    return command(['gcloud', 'compute', 'scp', str(local),
                    'root@' + member['name'] + ':' + dest,
                    *gcloud_flags(member)], timeout=timeout)


def synchronize_member(member, digests):
    label = member['name'] + ' (' + member['zone'] + ')'
    if member['status'] != 'RUNNING' or member['action'] != 'NONE':
        print('SKIP ' + label + ' : VM not stable (' + member['status'] + '/' + member['action'] + ')', flush=True)
        return 'skip'
    # Cheap idempotent preflight: do not copy or restart when both scripts are
    # already current and their services are running.  A failure falls back to
    # the transactional installer (which checks everything again).
    try:
        preflight = remote_ssh(
            member,
            'sha256sum /usr/local/lib/autokom-ha/worker-agent.py '
            '/usr/local/lib/autokom-ha/worker-health.py && '
            'systemctl is-active --quiet autokom-ha-worker-agent.service && '
            'systemctl is-active --quiet autokom-ha-worker-health.service',
            timeout=35,
        )
        digests_remote = [line.strip().split()[0] for line in preflight.splitlines() if len(line.strip().split()) >= 2]
        if len(digests_remote) == 2 and digests_remote == [digests['agent'], digests['health']]:
            print('OK ' + label + ' : up-to-date', flush=True)
            return 'up-to-date'
    except SyncError:
        pass
    # No secrets on CLI. Directory is root-owned and new for each member and run.
    remote_dir = '/run/autokom-ha/code-sync-' + uuid.uuid4().hex[:18]
    remote_ssh(member, 'install -d -m 0700 ' + remote_dir)
    try:
        remote_scp(member, SOURCE_MAP['agent'], remote_dir + '/agent.py')
        remote_scp(member, SOURCE_MAP['health'], remote_dir + '/health.py')
        remote_scp(member, SOURCE_MAP['apply'], remote_dir + '/apply.py')
        # Run code in RAM-backed /run; incoming file hashes checked by remote applier.
        call = ('python3 ' + remote_dir + '/apply.py --agent ' + remote_dir + '/agent.py'
                ' --health ' + remote_dir + '/health.py'
                ' --sha-agent ' + digests['agent'] + ' --sha-health ' + digests['health'])
        output = remote_ssh(member, call, timeout=100)
        # stdout may include cloud SSH warnings, but find the JSON status line.
        parsed = None
        for line in output.splitlines():
            if line.lstrip().startswith('{') and '"result"' in line:
                try:
                    parsed = json.loads(line)
                except ValueError:
                    pass
        if not parsed or parsed.get('result') not in ('updated', 'up-to-date'):
            raise SyncError('Worker did not return a verified sync status: ' + label)
        print('OK ' + label + ' : ' + parsed['result'], flush=True)
        return parsed['result']
    finally:
        try:
            remote_ssh(member, 'rm -rf -- ' + remote_dir, timeout=30)
        except SyncError:
            print('WARN ' + label + ' : temporary /run sync directory not removed', file=sys.stderr)


def install_timer():
    if os.geteuid() != 0:
        raise SyncError('Timer installation requires root')
    script = ROOT / 'scripts/lib/autokom_ha_worker_sync.py'
    if str(ROOT) != '/opt/autokom/source':
        raise SyncError('Install timer only on the GCP main controller at /opt/autokom/source')
    # One-time opt-in: the control plane owns a dedicated SSH key and a timer.
    body = (f'[Unit]\nDescription=AUTOKOM HA sync worker code across MIG\n'
            f'After=network-online.target\nWants=network-online.target\n\n'
            f'[Service]\nType=oneshot\nUser=root\n'
            f'ExecStart=/usr/bin/python3 {script} --apply\n'
            f'TimeoutStartSec=600\nNoNewPrivileges=yes\n'
            f'ProtectSystem=full\nPrivateTmp=yes\n\n')
    timer = ('[Unit]\nDescription=Keep AUTOKOM HA worker code synchronized\n\n'
             '[Timer]\nOnBootSec=3min\nOnUnitInactiveSec=15min\n'
             'RandomizedDelaySec=1min\nPersistent=true\nUnit=' + SERVICE_NAME + '\n\n'
             '[Install]\nWantedBy=timers.target\n')
    for name, value in ((SERVICE_NAME, body), (TIMER_NAME, timer)):
        target = UNIT_DIR / name
        if target.is_symlink():
            raise SyncError('Refusing systemd unit symlink ' + name)
        tmp = target.with_suffix('.new')
        tmp.write_text(value, encoding='utf-8')
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    command(['systemctl', 'daemon-reload'], timeout=30)
    command(['systemctl', 'enable', '--now', TIMER_NAME], timeout=30)
    print('Timer HA enabled: ' + TIMER_NAME)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Sync AUTOKOM worker scripts across the live GCP MIG')
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='Read-only discovery and source checks')
    group.add_argument('--apply', action='store_true', help='Sync all stable MIG workers one-by-one')
    group.add_argument('--install-timer', action='store_true', help='Install timer and sync immediately')
    opts = ap.parse_args(argv)
    try:
        if opts.check:
            digest = checked_files()
            print('AUTOKOM HA sync · sources valid · agent=' + digest['agent'][:12] +
                  ' health=' + digest['health'][:12])
            for member in discover():
                print(f"  {member['name']} {member['zone']} {member['status']} action={member['action']}")
            return 0
        digests = checked_files()
        members = discover()
        prepare_key()
        # Parallel callers (timer + autokom update) must not race or stomp backups.
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK, 'a+', encoding='utf-8') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('AUTOKOM HA worker sync already running; this run skipped')
                return 0
            if opts.install_timer:
                install_timer()
            results = []
            for member in members:
                results.append(synchronize_member(member, digests))
            print('AUTOKOM HA worker sync · ' +
                  ', '.join(f'{result}={results.count(result)}' for result in sorted(set(results))))
            return 0 if all(r in ('updated', 'up-to-date') for r in results) else 2
    except (SyncError, OSError, SyntaxError, ValueError) as exc:
        print('AUTOKOM_HA_WORKER_SYNC_ERROR ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
