#!/usr/bin/env python3
"""Allocate non-overlapping subordinate UID/GID ranges for AUTOKOM BuildKit.

Runs as root from a fixed Ansible role only; never accepts any path arguments.
An incomplete transaction safely resumes the missing range on next run.
"""
from __future__ import annotations
import fcntl
import os
from pathlib import Path
import re
import sys

COUNT = 65536
BEGIN = 500000
LIMIT = 2_000_000_000
NAME = re.compile(r'^[a-z_][a-z0-9_-]{0,30}$')


def ranges(path: Path):
    if not path.exists():
        return []
    result = []
    for row in path.read_text(encoding='ascii').splitlines():
        if not row or row.startswith('#'):
            continue
        cells = row.split(':')
        if len(cells) != 3:
            raise ValueError(f'{path.name}: entrée invalide')
        owner, beginning, count = cells
        if not (beginning.isdigit() and count.isdigit()):
            raise ValueError(f'{path.name}: plage invalide')
        a, n = int(beginning), int(count)
        if a < 0 or n < 1 or a + n > 2**32:
            raise ValueError(f'{path.name}: bornes invalides')
        result.append((owner, a, a+n))
    return result


def choose(existing):
    start = BEGIN
    occupied = sorted((a, b) for _, a, b in existing)
    for a, b in occupied:
        if start + COUNT <= a:
            break
        if start < b:
            start = b
    if start + COUNT > LIMIT:
        raise ValueError('plus de sous-identifiants disponibles')
    return start


def allocate(user, directory=Path('/etc')):
    if not NAME.fullmatch(user):
        raise ValueError('compte invalide')
    directory = Path(directory)
    # Own lock prevents two concurrent AUTOKOM provisions allocating the same range.
    lock_path = directory / '.autokom-buildkit-subids.lock'
    with lock_path.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        changed = False
        for basename in ('subuid', 'subgid'):
            path = directory / basename
            if path.is_symlink():
                raise ValueError(f'{basename}: symlink refusé')
            existing = ranges(path)
            owned = [(a, b) for owner, a, b in existing if owner == user]
            if owned:
                if len(owned) != 1 or owned[0][1] - owned[0][0] < COUNT:
                    raise ValueError(f'{basename}: plage existante insuffisante ou multiple')
                continue
            start = choose(existing)
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(path, flags, 0o644)
            try:
                os.write(fd, f'{user}:{start}:{COUNT}\n'.encode('ascii'))
                os.fsync(fd)
            finally:
                os.close(fd)
            changed = True
        return changed


def main(argv):
    if os.geteuid() != 0 or len(argv) != 2 or argv[1] != 'autokom-buildkit':
        return 2
    try:
        changed = allocate(argv[1])
    except (ValueError, OSError) as exc:
        print('AUTOKOM_SUBID:ERROR: ' + str(exc), file=sys.stderr)
        return 2
    print('AUTOKOM_SUBID:' + ('CHANGED' if changed else 'PRESENT'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
