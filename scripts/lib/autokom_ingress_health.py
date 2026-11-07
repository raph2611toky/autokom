#!/usr/bin/env python3
"""AUTOKOM public-ingress verification and guarded Nginx default reconciliation.

Treat a TLS certificate, a running upstream and a reachable public URL as
separate facts. Probe the REAL TLS/SNI vhost locally: a GCP VM's public IP is
NATed and is not a reliable same-VM test target.

Never rewrite or disable a third-party default server. Only the recognizable
Ubuntu stock welcome-site symlink may be disabled, with an atomic rollback on
nginx validation/reload failure. No automatic cloud firewall changes.
"""
from __future__ import annotations

from autokom_nginx_bind_policy import https_listens
from pathlib import Path
import os
import re
import json
import stat
import socket
import ssl
import subprocess

NGINX_AVAILABLE = Path('/etc/nginx/sites-available')
NGINX_ENABLED = Path('/etc/nginx/sites-enabled')
CATCHALL_NAME = '00-autokom-catchall.conf'
LEGACY_CATCHALL = '00-catchall.conf'
DEFAULT_NAME = 'default'

# Reject unknown hosts with a genuine 404, never the Ubuntu Nginx welcome page.
# Reject unknown TLS handshakes: an unrelated name must not receive the
# Jenkins, Grafana or Prometheus certificate.
CATCHALL = """# AUTOKOM MANAGED UNKNOWN-HOST CATCHALL v1
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    default_type text/plain;
    return 404;
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_reject_handshake on;
}
"""


class IngressError(RuntimeError):
    pass


def expected_catchall():
    """Reflect a verified, explicit TCP/443 per-address policy."""
    return CATCHALL.replace('    listen 443 ssl default_server;\n',
                            https_listens(flags='ssl default_server'))


def _run(args, *, timeout=12):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IngressError(f"Commande de diagnostic indisponible : {args[0]}") from exc


def _stock_default(path: Path) -> bool:
    """Recognize a stock Ubuntu welcome vhost; NEVER adopt a custom default."""
    if not path.is_symlink():
        return False
    try:
        destination = path.resolve(strict=True)
        if destination != (NGINX_AVAILABLE / DEFAULT_NAME) or not destination.is_file():
            return False
        data = destination.read_text(encoding='utf-8')
        if len(data) > 80_000:
            return False
        clean = '\n'.join(line.split('#', 1)[0] for line in data.splitlines())
        # Protect applications and user modifications, including included files.
        if re.search(r'\b(proxy_pass|fastcgi_pass|uwsgi_pass|grpc_pass|return|rewrite|alias|include|ssl_certificate)\b', clean):
            return False
        return all(re.search(pattern, clean) for pattern in (
            r'\blisten\s+80\s+default_server\s*;',
            r'\bserver_name\s+_\s*;',
            r'\broot\s+/var/www/html\s*;',
            r'\btry_files\s+\$uri\s+\$uri/\s+=404\s*;',
        ))
    except (OSError, UnicodeError):
        return False


def _defaults_from_nginx_dump(output: str) -> set[str]:
    """Parse nginx -T provenance without displaying its (possibly private) data."""
    location = '<unknown>'
    found = set()
    for line in output.splitlines():
        match = re.match(r'^# configuration file (.+):$', line)
        if match:
            location = match.group(1)
        code = line.split('#', 1)[0]
        if re.search(r'^\s*listen\s+[^;]*\bdefault_server\s*;', code):
            found.add(location)
    return found


def catchall_ready(*, enabled: Path | None = None, available: Path | None = None,
                   default: Path | None = None) -> bool:
    enabled = enabled or NGINX_ENABLED / CATCHALL_NAME
    available = available or NGINX_AVAILABLE / CATCHALL_NAME
    default = default or NGINX_ENABLED / DEFAULT_NAME
    try:
        if default.exists() or default.is_symlink():
            return False
        if (enabled.is_symlink() and enabled.resolve(strict=True) == available and
                available.is_file() and not available.is_symlink() and
                available.read_text(encoding='utf-8') == expected_catchall()):
            return True
        # Historical AUTOKOM security module used a 444 catch-all. It is
        # secure and must not be replaced merely to change the error code.
        legacy_en = enabled.parent / LEGACY_CATCHALL
        legacy_av = available.parent / LEGACY_CATCHALL
        if (legacy_en.is_symlink() and legacy_en.resolve(strict=True) == legacy_av and
                legacy_av.is_file() and not legacy_av.is_symlink()):
            legacy = legacy_av.read_text(encoding='utf-8')
            return ('return 444;' in legacy or 'return 404;' in legacy) and 'ssl_reject_handshake on;' in legacy and 'default_server' in legacy
        return False
    except (OSError, UnicodeError):
        return False


def reconcile_catchall(*, check=False, enabled_dir=None, available_dir=None):
    """Enable AUTOKOM fallback, removing ONLY the stock Ubuntu default symlink.

    When updating Nginx, work transactionally. A bad new config or reload
    restores the previous enabled sites. The vendor default file is never
    altered or deleted.
    """
    ena = Path(enabled_dir) if enabled_dir is not None else NGINX_ENABLED
    ava = Path(available_dir) if available_dir is not None else NGINX_AVAILABLE
    default = ena / DEFAULT_NAME
    target = ava / CATCHALL_NAME
    link = ena / CATCHALL_NAME
    if catchall_ready(enabled=link, available=target, default=default):
        return 'conforme'
    if _run(['nginx', '-t']).returncode:
        raise IngressError('nginx -t échoue déjà : aucune modification possible')
    if default.exists() or default.is_symlink():
        if not _stock_default(default):
            raise IngressError('Default Nginx personnalisé ou inconnu : non modifié. '
                               'Inspecter sudo nginx -T et décider explicitement de sa migration.')
    if target.exists() or target.is_symlink() or link.exists() or link.is_symlink():
        raise IngressError('Fichier catch-all AUTOKOM existant non conforme : refus de remplacement')
    p = _run(['nginx', '-T'], timeout=20)
    if p.returncode:
        raise IngressError('nginx -T impossible : absence de modification')
    defaults = _defaults_from_nginx_dump(p.stdout + '\n' + p.stderr)
    permitted = {'/etc/nginx/sites-enabled/default', '/etc/nginx/sites-available/default',
                 str(default), str(ava / DEFAULT_NAME)}
    if defaults - permitted:
        raise IngressError('Autre default_server actif : refus de conflit ; '
                           'vérifier les directives listen ... default_server via nginx -T')
    if check:
        return 'à créer (site Ubuntu standard à désactiver)' if default.is_symlink() else 'à créer'
    if os.geteuid() != 0:
        raise IngressError('Reconfiguration Nginx : utiliser sudo autokom install')
    ena.mkdir(parents=True, exist_ok=True)
    ava.mkdir(parents=True, exist_ok=True)
    stock_link = os.readlink(default) if default.is_symlink() else None
    created_target = False
    created_link = False
    try:
        # Exclusive creation ensures a concurrent operator cannot be overwritten.
        with target.open('x', encoding='utf-8') as output:
            output.write(expected_catchall())
        created_target = True
        os.chmod(target, 0o644)
        if stock_link is not None:
            default.unlink()
        link.symlink_to(target)
        created_link = True
        if _run(['nginx', '-t']).returncode:
            raise IngressError('nginx -t refuse le nouveau catch-all')
        if _run(['systemctl', 'reload', 'nginx'], timeout=20).returncode:
            raise IngressError('Échec du rechargement Nginx après catch-all')
        return 'activé (404 hôtes inconnus, TLS inconnu refusé)'
    except Exception:
        if created_link and link.is_symlink() and os.readlink(link) == str(target):
            link.unlink()
        if created_target and target.is_file():
            target.unlink()
        if stock_link is not None and not default.exists() and not default.is_symlink():
            default.symlink_to(stock_link)
        # Best effort; do not hide original failure.
        _run(['nginx', '-t'])
        _run(['systemctl', 'reload', 'nginx'], timeout=20)
        raise



def managed_domains(settings, *, state_path=None, selection_path=None):
    """Resolve actual chosen domains, even if observability.ini is unchanged.

    AUTOKOM already persists domain names separately from the INI. Read only
    root-owned/private AUTOKOM records; an untrusted file cannot make a network
    probe target an attacker-selected host.
    """
    state_path = Path(state_path) if state_path is not None else Path('/etc/autokom/state/observability.json')
    selection_path = Path(selection_path) if selection_path is not None else Path('/var/lib/autokom/install/observability-selection.json')
    # The most recent interactive selection is authoritative, including an
    # explicit blank domain (local-only). A stale INI/state must not override it.
    result = {'prometheus': None, 'grafana': None}
    unreadable_private = False
    for candidate in (selection_path, state_path):
        try:
            if candidate.is_symlink():
                continue
            info = candidate.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                continue
            if info.st_size > 16_384:
                continue
            record = json.loads(candidate.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or record.get('schema') != 1:
                continue
            for kind in ('prometheus', 'grafana'):
                key = kind + '_domain'
                if result[kind] is None and isinstance(record.get(key), str):
                    value = record[key].strip().lower()
                    if not value or re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+', value):
                        result[kind] = value
        except PermissionError:
            unreadable_private = True
            continue
        except (OSError, ValueError, UnicodeError):
            continue
    if unreadable_private and all(result[kind] is None and not getattr(settings, kind+'_domain', '')
                                  for kind in ('prometheus','grafana')):
        raise IngressError('État privé des domaines non lisible : consulter avec sudo')
    return {kind: value if value is not None else (getattr(settings, kind+'_domain', '') or '')
            for kind, value in result.items()}

def _local_ip():
    """Get the routed NIC's local IPv4, not metadata or the public NAT IP."""
    try:
        p = _run(['ip', '-4', 'route', 'get', '1.1.1.1'])
        if p.returncode == 0:
            m = re.search(r'\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})', p.stdout)
            if m:
                socket.inet_aton(m.group(1))
                return m.group(1)
    except (IngressError, OSError):
        pass
    return ''


def probe_https(domain: str, *, addresses=None, timeout=3):
    """Return (status, address, errors) for a *trusted* local HTTPS/SNI probe.

    The certificate must pass ordinary CA and hostname checks. No external-IP
    self-connect; GCP 1:1 NAT is not a localhost proxy.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{1,250}[A-Za-z0-9]', domain):
        return 0, '', ('domaine invalide',)
    if addresses is None:
        addresses = ('127.0.0.1', _local_ip())
    errors = []
    for host in dict.fromkeys(a for a in addresses if a):
        try:
            with socket.create_connection((host, 443), timeout=timeout) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=domain) as secure:
                    secure.settimeout(timeout)
                    secure.sendall(f'HEAD / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n'.encode('ascii'))
                    line = b''
                    while b'\n' not in line and len(line) < 256:
                        chunk = secure.recv(1)
                        if not chunk:
                            break
                        line += chunk
                    found = re.match(rb'^HTTP/\d(?:\.\d)?\s+(\d{3})\b', line)
                    if not found:
                        raise OSError('réponse HTTP absente')
                    return int(found.group(1)), host, tuple(errors)
        except (OSError, ssl.SSLError, TimeoutError, ValueError) as exc:
            errors.append(f'{host}: {type(exc).__name__}')
    return 0, '', tuple(errors)


def probe_http(domain: str, *, addresses=None, timeout=3):
    """Probe the actual Host-routed HTTP listener, without public-IP hairpin."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{1,250}[A-Za-z0-9]', domain):
        return 0, '', ('domaine invalide',)
    if addresses is None:
        addresses = ('127.0.0.1', _local_ip())
    errors = []
    for host in dict.fromkeys(a for a in addresses if a):
        try:
            with socket.create_connection((host, 80), timeout=timeout) as conn:
                conn.settimeout(timeout)
                conn.sendall(f'HEAD / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n'.encode('ascii'))
                line = b''
                while b'\n' not in line and len(line) < 256:
                    chunk = conn.recv(1)
                    if not chunk:
                        break
                    line += chunk
                match = re.match(rb'^HTTP/\d(?:\.\d)?\s+(\d{3})\b', line)
                if not match:
                    raise OSError('réponse HTTP absente')
                return int(match.group(1)), host, tuple(errors)
        except (OSError, TimeoutError, ValueError) as exc:
            errors.append(f'{host}: {type(exc).__name__}')
    return 0, '', tuple(errors)


def public_vhost_healthy(domain: str, kind: str, *, probe=probe_https, http_probe=probe_http) -> bool:
    if not domain:
        return True
    code, _, _ = probe(domain)
    http_code, _, _ = http_probe(domain)
    if http_code not in (301, 308):
        return False
    if kind == 'prometheus':
        # Basic authentication must actually protect Prometheus over HTTPS.
        return code == 401
    if kind == 'grafana':
        return code in (200, 301, 302, 303, 307, 308, 401)
    if kind == 'jenkins':
        return code in (200, 301, 302, 303, 403)
    return False


def verify_observability(domains: list[tuple[str, str]], *, probe=probe_https,
                         http_probe=probe_http):
    """Fail closed: installed != locally reachable. Remote VPC is independent."""
    failures = []
    for kind, domain in domains:
        if not domain:
            continue
        code, addr, errors = probe(domain)
        ok = ((code == 401) if kind == 'prometheus' else code in (200, 301, 302, 303, 307, 308, 401))
        http_code, http_address, http_errors = http_probe(domain)
        if not ok:
            diagnostic = ', '.join(errors) or f'HTTP {code}'
            failures.append(f'{domain}: HTTPS/SNI local invalide ({diagnostic}; code={code})')
        if http_code not in (301,308):
            diagnostic = ', '.join(http_errors) or f'HTTP {http_code}'
            failures.append(f'{domain}: HTTP/80 ne redirige pas vers HTTPS ({diagnostic})')
    if failures:
        raise IngressError('; '.join(failures) + '. Vérifier autokom tools nginx logs --lines 60, '
                           'les listeners 80/443 et les virtual hosts actifs; le VPC doit être testé depuis Kali.')
    return True
