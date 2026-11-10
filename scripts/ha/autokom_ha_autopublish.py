#!/usr/bin/env python3
"""AUTOKOM HA: reliable, idempotent image promotion queue, never a client cutover.

Only broker-created, root-owned records representing committed AUTOKOM releases are
accepted. A failed Artifact Registry push retries on the next systemd timer run.
Publishing an image alone is never advertised as successful application HA.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

from autokom_ha_registry import (PROJECT, REGION, REPO, PromotionError,
                                 image_tag, metadata_token, check_repository,
                                 metadata_identity, registry_manifest_digest,
                                 publish)

UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
ROOT = Path('/var/lib/autokom/client-runtime')
QUEUE = Path('/var/lib/autokom/ha/publish-queue')
RESULTS = Path('/var/lib/autokom/ha/published')
LOCK = Path('/run/lock/autokom-ha-publisher.lock')


class QueueError(RuntimeError):
    pass


def _valid_uuid(value: object) -> str:
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise QueueError('Invalid canonical UUID')
    return value


def _safe_file(path: Path) -> dict:
    if path.is_symlink():
        raise QueueError('Symlink denied')
    st = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4096 or (st.st_mode & 0o022) or st.st_uid != 0:
        raise QueueError('Queue file owner, type or mode invalid')
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise QueueError('Invalid queue payload')
    _valid_uuid(obj.get('run_id'))
    _valid_uuid(obj.get('environment_id'))
    if path.name != obj['run_id'] + '.json':
        raise QueueError('Queue name mismatch')
    return obj


def _active_run(environment_id: str, run_id: str, root: Path = ROOT) -> bool:
    """Never promote a rolled back or deprovisioned release."""
    state = root / environment_id / 'current.json'
    if state.is_symlink() or not state.is_file():
        return False
    try:
        value = json.loads(state.read_text(encoding='utf-8'))
        return value.get('environment_id') == environment_id and value.get('run_id') == run_id
    except (ValueError, OSError, AttributeError):
        return False


def _atomic_result(directory: Path, run_id: str, digest_ref: str) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {'run_id': run_id, 'image': digest_ref, 'published_at': int(time.time())}
    fd, temp = tempfile.mkstemp(prefix='.autokom-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(result, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, directory / (run_id + '.json'))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def process_one(path: Path, *, token: str, queue: Path = QUEUE,
                results: Path = RESULTS, root: Path = ROOT) -> str:
    data = _safe_file(path)
    rid, env = data['run_id'], data['environment_id']
    if not _active_run(env, rid, root):
        # Keep unmatched queue entry for diagnostic; remove only if no release exists.
        raise QueueError('Not current; no image published (old/rolled back run)')
    image = image_tag(PROJECT, REGION, REPO, rid)
    digest = registry_manifest_digest(image, token)
    if digest is None:
        digest_ref = publish(rid, PROJECT, REGION, REPO, from_archive=True,
                             identity='', token=token)
    else:
        digest_ref = image.split(':', 1)[0] + '@' + digest
    _atomic_result(results, rid, digest_ref)
    # Queue item may be removed only after the durable published result exists.
    path.unlink()
    return digest_ref


def discover_local_releases(root: Path = ROOT, queue: Path = QUEUE,
                            results: Path = RESULTS, artifacts: Path = Path('/var/lib/autokom/build-artifacts')) -> int:
    """Enqueue existing committed local releases; no operator migration step.

    This never changes the active Docker container, release, DNS or client URL.
    Only unambiguous root-owned current.json with a trusted Jenkins TAR is used.
    """
    if not root.is_dir():
        return 0
    queue.mkdir(parents=True, exist_ok=True, mode=0o700)
    count = 0
    for folder in sorted(root.iterdir()):
        if count >= 100:
            break
        if folder.is_symlink() or not folder.is_dir() or not UUID.fullmatch(folder.name):
            continue
        # Previous pending means local release/rollback transaction unfinished.
        if (folder / 'previous.json').exists():
            continue
        file = folder / 'current.json'
        if file.is_symlink() or not file.is_file():
            continue
        try:
            st = file.stat(follow_symlinks=False)
            if st.st_uid != 0 or st.st_mode & 0o022 or st.st_size > 8192:
                continue
            doc = json.loads(file.read_text(encoding='utf-8'))
            rid = _valid_uuid(doc.get('run_id'))
            if doc.get('environment_id') != folder.name:
                continue
        except (ValueError, OSError, QueueError, AttributeError):
            continue
        candidate = artifacts / (rid + '.tar')
        if candidate.is_symlink() or not candidate.is_file():
            # Existing images can have been pruned. Do not try to rebuild them
            # or expose a GitHub dependency in this registry-only process.
            continue
        if (results / (rid + '.json')).is_file() or (queue / (rid + '.json')).exists():
            continue
        fd, tmp = tempfile.mkstemp(prefix='.autokom-', dir=queue)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({'run_id': rid, 'environment_id': folder.name}, stream)
                stream.write('\n')
            os.replace(tmp, queue / (rid + '.json'))
            count += 1
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return count


def scan(queue: Path = QUEUE, results: Path = RESULTS, root: Path = ROOT,
         *, dry_run: bool = False) -> tuple[int, int]:
    if not dry_run:
        discover_local_releases(root, queue, results)
    if not queue.is_dir():
        return 0, 0
    # Bound upload I/O on the production controller and fairly retry failures.
    candidates = sorted(queue.glob('*.json'), key=lambda p: (p.stat().st_mtime, p.name))[:2]
    if not candidates:
        return 0, 0
    checked = [_safe_file(path) for path in candidates]
    if dry_run:
        for doc in checked:
            print('A publier (sans appliquer) : ' + doc['run_id'])
        return 0, 0
    if os.geteuid() != 0:
        raise QueueError('Root required to read trusted Docker build artifacts')
    token = metadata_token()
    check_repository(PROJECT, REGION, REPO, token)
    successes, errors = 0, 0
    for path in candidates:
        try:
            digest_ref = process_one(path, token=token, queue=queue, results=results, root=root)
            print('AUTOKOM_HA_IMAGE_PUBLISHED ' + path.stem + ' digest=' + digest_ref.rsplit('@', 1)[-1][:19], flush=True)
            successes += 1
        except (QueueError, PromotionError, OSError, ValueError, RuntimeError) as exc:
            # Reason is a static type, no token, image, env or secret values.
            print('AUTOKOM_HA_IMAGE_PENDING run=' + path.stem + ' reason=' + type(exc).__name__,
                  file=sys.stderr, flush=True)
            try:
                os.utime(path, None)  # retry later, do not starve other queued runs
            except OSError:
                pass
            errors += 1
    return successes, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description='AUTOKOM automatic image promotion worker')
    parser.add_argument('--check', action='store_true', help='Readonly queue inventory')
    args = parser.parse_args(argv)
    if os.geteuid() != 0 and not args.check:
        parser.error('Run service as root')
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOCK, 'a+', encoding='utf-8') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ok, failed = scan(dry_run=args.check)
            print(f'AUTOKOM HA automatic publishing: published={ok} pending_errors={failed}')
            return 1 if failed else 0
    except BlockingIOError:
        print('AUTOKOM_HA_PUBLISH_BUSY another instance running')
        return 0
    except (QueueError, PromotionError, OSError, ValueError) as exc:
        print('AUTOKOM_HA_PUBLISH_FAILED ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
