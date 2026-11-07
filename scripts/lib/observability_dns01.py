"""Cloudflare DNS-01 for AUTOKOM observability, with secure unattended renewal.

Only uses an *already configured* zone-scoped Cloudflare token. Never creates
or changes A/AAAA/CNAME records, and never executes an HTTP-01 challenge when
DNS-01 is selected. Certbot owns the ephemeral _acme-challenge TXT lifecycle.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

CLOUDFLARE_CONFIG = Path('/etc/autokom/secrets/cloudflare-dns.env')
CERTBOT_PRIVATE_DIR = Path('/etc/letsencrypt/autokom')
CERTBOT_CREDENTIALS = CERTBOT_PRIVATE_DIR / 'cloudflare-dns.ini'
MARKER = '# AUTOKOM MANAGED Cloudflare DNS-01 credentials v1\n'
PLUGIN_PACKAGE = 'python3-certbot-dns-cloudflare'


class DNS01Error(RuntimeError):
    """Sanitized error, never include credentials or API responses."""


def _root_private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise DNS01Error(f'Fichier DNS-01 non régulier ou lien symbolique : {path}')
    st = path.stat()
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) & 0o077:
        raise DNS01Error(f'Fichier DNS-01 doit appartenir à root (0600) : {path}')


def configured_zone_credentials(domain: str, *, env_path: Path | None = None):
    """Return token and zone only for a host in the existing Cloudflare zone.

    `None` means there is no Cloudflare config and HTTP-01 is still supported.
    A file present but malformed is a hard failure: never silently downgrade
    security or repeatedly contact Let's Encrypt when admin intent is unclear.
    """
    env_path = CLOUDFLARE_CONFIG if env_path is None else Path(env_path)
    if not env_path.exists() and not env_path.is_symlink():
        return None
    _root_private_file(env_path)
    from autokom_dns_cloudflare import dotenv
    try:
        data = dotenv(env_path)
    except Exception as exc:
        raise DNS01Error('Configuration Cloudflare existante illisible ou non privée') from exc
    zone = str(data.get('CLOUDFLARE_ZONE_NAME', '')).strip().lower().rstrip('.')
    domain = str(domain).strip().lower().rstrip('.')
    if not zone or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', zone):
        raise DNS01Error('CLOUDFLARE_ZONE_NAME absent ou invalide (cloudflare-dns.env)')
    if not (domain == zone or domain.endswith('.' + zone)):
        return None
    token = str(data.get('CLOUDFLARE_API_TOKEN', ''))
    zone_id = str(data.get('CLOUDFLARE_ZONE_ID', ''))
    if not token or any(c.isspace() or ord(c) < 33 for c in token):
        raise DNS01Error('Jeton API Cloudflare manquant/invalide ; aucun secret affiché')
    if not re.fullmatch(r'[0-9a-fA-F]{32}', zone_id):
        raise DNS01Error('CLOUDFLARE_ZONE_ID absent/invalide ; vérifier cloudflare-dns.env')
    return (zone, token)


def ensure_certbot_credentials(token: str, *, folder: Path | None = None) -> Path:
    """Write only AUTOKOM's root:0600 credentials, preserving foreign files."""
    folder = CERTBOT_PRIVATE_DIR if folder is None else Path(folder)
    if folder.is_symlink():
        raise DNS01Error(f'Dossier Certbot AUTOKOM est un lien symbolique : {folder}')
    if not folder.exists():
        # /etc/letsencrypt is already provisioned by Certbot on the target VM.
        if folder.parent.is_symlink() or not folder.parent.is_dir():
            raise DNS01Error('Répertoire /etc/letsencrypt non fiable ou absent')
        folder.mkdir(mode=0o700)
    st = folder.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
        raise DNS01Error(f'Dossier Certbot AUTOKOM doit être root:0700 : {folder}')
    dest = folder / 'cloudflare-dns.ini'
    contents = MARKER + 'dns_cloudflare_api_token = ' + token + '\n'
    if dest.exists() or dest.is_symlink():
        _root_private_file(dest)
        existing = dest.read_text(encoding='utf-8')
        if not existing.startswith(MARKER):
            raise DNS01Error(f'Fichier Cloudflare Certbot non géré par AUTOKOM : {dest}')
        if existing == contents:
            return dest
        # Token rotation: atomic replacement, *only* for a known AUTOKOM file.
        stage = folder / '.cloudflare-dns.ini.next'
        if stage.exists() or stage.is_symlink():
            raise DNS01Error('Fichier temporaire de credentials DNS-01 déjà présent : vérifier avant reprise')
        _write_no_follow(stage, contents)
        try:
            stage.replace(dest)
        except Exception:
            stage.unlink(missing_ok=True)
            raise
    else:
        _write_no_follow(dest, contents)
    return dest


def _write_no_follow(path: Path, contents: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def plugin_available(*, command='certbot') -> bool:
    try:
        probe = subprocess.run([command, 'plugins', '--text'], capture_output=True,
                               text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0 and bool(re.search(r'(?m)^\s*\*\s*dns-cloudflare\s*$', probe.stdout))


def ensure_dns_plugin(run_command, *, check=plugin_available) -> None:
    """Install plugin only when missing; refuse incompatible Certbot environments."""
    if check():
        return
    try:
        run_command(['apt-get', 'install', '-y', '--no-install-recommends', PLUGIN_PACKAGE], timeout=300)
    except Exception as exc:
        # Missing/stale APT indices are frequent on freshly provisioned VMs.
        # Retry exactly once after refreshing package metadata; do not alter
        # repositories or introduce pip/snap into the system Python runtime.
        try:
            run_command(['apt-get', 'update'], timeout=300)
            run_command(['apt-get', 'install', '-y', '--no-install-recommends', PLUGIN_PACKAGE], timeout=300)
        except Exception as final:
            raise DNS01Error('Impossible d’installer le plugin officiel Certbot Cloudflare via APT ; '
                             'vérifier le dépôt Ubuntu universe et apt-cache policy. '
                             'Aucun site ni certificat modifié.') from final
    if not check():
        raise DNS01Error('Certbot ne détecte pas dns-cloudflare après installation ; vérifier '
                         'le chemin Certbot (Snap/APT) et ses plugins. Pas de remplacement automatique.')


def certbot_dns01_args(domain: str, credentials: Path) -> list[str]:
    return ['--dns-cloudflare', '--dns-cloudflare-credentials', str(credentials),
            '--dns-cloudflare-propagation-seconds', '60', '-d', domain]
