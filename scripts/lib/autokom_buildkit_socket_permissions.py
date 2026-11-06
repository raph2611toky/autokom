#!/usr/bin/env python3
"""Root-only, fixed-path socket permission handoff after rootlesskit starts.

Never accept an arbitrary path or group from the CLI; never touch Docker sockets.
"""
import ctypes
import grp
import os
from pathlib import Path
import platform
import pwd
import stat
import sys
import time

DIRECTORY = Path('/run/autokom-buildkit')
SOCKET = DIRECTORY / 'buildkitd.sock'


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        return 2
    builder = grp.getgrnam('autokom-builder').gr_gid
    daemon = pwd.getpwnam('autokom-buildkit').pw_uid
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        if DIRECTORY.is_symlink():
            return 3
        info = DIRECTORY.stat() if DIRECTORY.exists() else None
        if info is None or info.st_uid != daemon or info.st_gid != builder or stat.S_IMODE(info.st_mode) != 0o750:
            return 3
        try:
            file = SOCKET.lstat()
        except FileNotFoundError:
            time.sleep(0.25)
            continue
        if not stat.S_ISSOCK(file.st_mode) or file.st_uid != daemon:
            return 4
        # Use dirfd + NOFOLLOW: never chmod a symlink through a compromised daemon.
        # fchmodat2 is available from Linux 6.6 (Ubuntu 24.04 kernel >= 6.8).
        if platform.machine() not in ('x86_64', 'aarch64'):
            return 7
        directory_fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.chown('buildkitd.sock', -1, builder, dir_fd=directory_fd, follow_symlinks=False)
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            # fchmodat2 syscall 452 on both x86_64 and aarch64, AT_SYMLINK_NOFOLLOW=0x100
            rv = libc.syscall(ctypes.c_long(452), ctypes.c_int(directory_fd),
                              ctypes.c_char_p(b'buildkitd.sock'),
                              ctypes.c_uint(0o660), ctypes.c_int(0x100))
            if rv != 0:
                raise OSError(ctypes.get_errno(), 'fchmodat2 socket failure')
        finally:
            os.close(directory_fd)
        after = SOCKET.lstat()
        if after.st_ino != file.st_ino or after.st_gid != builder or stat.S_IMODE(after.st_mode) != 0o660:
            return 4
        return 0
    return 5


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, KeyError):
        raise SystemExit(6)
