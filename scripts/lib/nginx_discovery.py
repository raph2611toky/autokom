#!/usr/bin/env python3
"""Safe, read-only introspection of active Nginx server names and Jenkins TLS.

Never logs the full `nginx -T` output (it may include credentials).
"""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

@dataclass(frozen=True)
class Site:
    name: str
    proxy_ok: bool
    tls_configured: bool
    tls_valid: bool
    cert_path: str = ''


def blocks(config: str, kind: str) -> list[str]:
    # nginx -T returns flattened text with provenance lines, which is sufficient
    # for an inventory; braces inside comments are stripped first.
    text = '\n'.join(line.split('#', 1)[0] for line in config.splitlines())
    matches = []
    for m in re.finditer(r'(?<![\w-])' + re.escape(kind) + r'\s+(?:[\w.-]+\s*)?\{', text):
        level = 1
        i = m.end()
        start = i
        while i < len(text) and level:
            if text[i] == '{':
                level += 1
            elif text[i] == '}':
                level -= 1
            i += 1
        if level == 0:
            matches.append(text[start:i - 1])
    return matches


def matches_domain(server: str, domain: str) -> bool:
    for raw in re.findall(r'(?m)^\s*server_name\s+([^;]+);', server):
        if domain.lower() in [n.lower() for n in raw.split()]:
            return True
    return False


def nginx_sites(config: str, check_cert=None) -> dict[str, Site]:
    """Inventory exact domains served by Jenkins, and all non-Jenkins names.

    check_cert is callable(path, domain)->bool; defaults to False if unknown.
    """
    upstreams = {}
    for m in re.finditer(r'(?m)^\s*upstream\s+([\w.-]+)\s*\{', config):
        # Simple regex is safe for one-level upstream declarations.
        body = config[m.end():].split('}', 1)[0]
        upstreams[m.group(1)] = bool(re.search(r'\bserver\s+(?:127\.0\.0\.1|localhost):8080\s*;', body))
    domains: dict[str, list[str]] = {}
    for server in blocks(config, 'server'):
        for value in re.findall(r'(?m)^\s*server_name\s+([^;]+);', server):
            for domain in value.split():
                if domain.startswith(('$', '~', '*', '_')):
                    continue
                domains.setdefault(domain.lower(), []).append(server)
    result = {}
    for domain, servers in domains.items():
        # Only reuse a hostname if its HTTPS virtual host actually forwards to
        # Jenkins, not merely if *another* HTTP vhost on the same name does.
        https_proxy_ok = False
        tls_configured = False
        tls_valid = False
        cert_path = ''
        for server in servers:
            targets = re.findall(r'\bproxy_pass\s+https?://([^/;\s]+)', server)
            this_proxy = any(t in ('127.0.0.1:8080', 'localhost:8080', '[::1]:8080')
                             or upstreams.get(t, False) for t in targets)
            https_listen = bool(re.search(r'(?m)^\s*listen\s+(?:\[::\]:)?443\b[^;]*\bssl\b', server))
            certificate = re.search(r'(?m)^\s*ssl_certificate\s+([^;]+);', server)
            if https_listen:
                https_proxy_ok |= this_proxy
            if https_listen and certificate:
                tls_configured = True
                path = certificate.group(1).strip().strip('"\'')
                valid = bool(check_cert(path, domain)) if check_cert else False
                if this_proxy and valid:
                    https_proxy_ok = True
                    tls_valid = True
                    cert_path = path
        result[domain] = Site(domain, https_proxy_ok, tls_configured, tls_valid, cert_path)
    return result


def verify_certificate(path: str, domain: str) -> bool:
    cert = Path(path)
    if not cert.is_file():
        return False
    for args in (['openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '604800'],
                 ['openssl', 'x509', '-in', str(cert), '-noout', '-checkhost', domain]):
        try:
            r = subprocess.run(args, capture_output=True, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if r.returncode != 0:
            return False
    return True


def inventory() -> dict[str, Site]:
    """Read active nginx -T configuration; do not use filenames alone."""
    try:
        proc = subprocess.run(['nginx', '-T'], text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Impossible de lire la configuration active Nginx : {type(exc).__name__}') from exc
    if proc.returncode:
        raise RuntimeError('Nginx -T a échoué ; corriger la configuration avant de gérer un domaine')
    return nginx_sites(proc.stdout, verify_certificate)


def jenkins_domains(sites: dict[str, Site]) -> list[str]:
    return sorted(s.name for s in sites.values() if s.proxy_ok and s.tls_configured and s.tls_valid)


def domain_plan(domain: str, sites: dict[str, Site]) -> str:
    """existing | new; reject ownership conflicts and defective existing TLS."""
    site = sites.get(domain.lower())
    if not site:
        return 'new'
    if not site.proxy_ok:
        raise ValueError(f'Domaine {domain} déjà utilisé par un autre service Nginx : aucune modification')
    if not site.tls_configured or not site.tls_valid:
        raise ValueError(f'Domaine {domain} existant, mais TLS absent/invalide/expire bientôt : corriger le certificat avant de poursuivre')
    return 'existing'
