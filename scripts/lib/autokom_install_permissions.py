"""Shared root-only state directory hardening for AUTOKOM installers.

Repair the legacy 0755 mode created by older AUTOKOM services code, while
refusing symlinks, foreign ownership and directories writable by other users.
"""
import os
import stat
from pathlib import Path


def private_install_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('Répertoire d’installation AUTOKOM : lien symbolique interdit')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise RuntimeError('Répertoire d’installation AUTOKOM non fiable') from error
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
            raise RuntimeError('Répertoire d’installation AUTOKOM : propriétaire inattendu')
        if st.st_mode & 0o022:
            raise RuntimeError('Répertoire d’installation AUTOKOM : écriture groupe/autres détectée ; intervention administrateur requise')
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o700)
        current = os.fstat(fd)
        if current.st_uid != os.geteuid() or current.st_mode & 0o077:
            raise RuntimeError('Répertoire d’installation AUTOKOM non privé après correction')
    finally:
        os.close(fd)
    return path
