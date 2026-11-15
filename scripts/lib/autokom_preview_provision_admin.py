"""Install just the root-only systemd entry point; provision lazily on demand."""
from pathlib import Path
import os
import stat
import subprocess

from autokom_wireguard import atomic

UNIT = 'autokom-preview-provision.service'
MAIN = Path('/opt/autokom/source')
STAGING = Path('/opt/autokom-preprod/source')
SYSTEM = Path('/etc/systemd/system')


class ProvisionInstallError(RuntimeError):
    pass


def install(source):
    source = Path(source).absolute()
    if os.geteuid() or source not in (MAIN, STAGING):
        raise ProvisionInstallError('AUTOKOM_PREVIEW_PROVISION_SOURCE')
    # The shared provisioner prefers the main checkout when both are installed.
    def secure_checkout(root):
        for p in (root, root.parent, root / 'scripts', root / 'scripts/wireguard',
                  root / 'scripts/lib', root / 'scripts/wireguard/provision.py',
                  root / 'scripts/wireguard/setup.sh'):
            info = p.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ProvisionInstallError('AUTOKOM_PREVIEW_PROVISION_SOURCE')
    if (MAIN / 'scripts/wireguard/provision.py').is_file():
        source = MAIN
    secure_checkout(source)
    template = source / 'scripts/wireguard' / UNIT
    if template.is_symlink() or template.stat().st_uid != 0 or template.stat().st_mode & 0o022:
        raise ProvisionInstallError('AUTOKOM_PREVIEW_PROVISION_SOURCE')
    dest = SYSTEM / UNIT
    text = template.read_text().replace('@AUTOKOM_SOURCE@', str(source))
    if dest.is_symlink() or (dest.exists() and (dest.stat().st_uid != 0 or dest.stat().st_mode & 0o022)):
        raise ProvisionInstallError('AUTOKOM_PREVIEW_PROVISION_UNIT')
    old = dest.read_text() if dest.is_file() else None
    if old == text:
        return
    if old is not None and not old.startswith('[Unit]\nDescription=AUTOKOM automatic private preview provisioning\n'):
        raise ProvisionInstallError('AUTOKOM_PREVIEW_PROVISION_UNIT')
    atomic(dest, text, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=25)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        raise SystemExit(2)
    install(sys.argv[1])
