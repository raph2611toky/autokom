#!/usr/bin/env python3
"""Opt-in migration from HTTP Basic to existing, healthy Authelia AuthRequest.

AUTOKOM DOES NOT deploy an auth server, create users, or bypass authorization.
The command is intentionally non-destructive on failure and fails closed.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import urllib.error

AUTHELIA_PORT=9091


def authelia_probe(domain, port=AUTHELIA_PORT):
    """Require a working unauthenticated denial with an HTTPS login redirect."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, target):return None
    url=f'http://127.0.0.1:{port}/api/authz/auth-request'
    headers={
        'X-Original-Method':'GET',
        'X-Original-URL':f'https://{domain}/',
        'X-Forwarded-Proto':'https',
        'X-Forwarded-Host':domain,
        'X-Forwarded-For':'127.0.0.1',
    }
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(urllib.request.Request(url,headers=headers),timeout=5) as response:
            return False
    except urllib.error.HTTPError as exc:
        # A login denial must redirect to a trusted HTTPS URL; never enable
        # a proxy that accidentally returns 2xx without authentication.
        location=exc.headers.get('Location','')
        from urllib.parse import urlsplit
        dest=urlsplit(location)
        family='.'.join(domain.split('.')[-2:])
        return (exc.code==401 and dest.scheme=='https' and dest.hostname is not None and
                (dest.hostname==family or dest.hostname.endswith('.'+family)) and
                '\n' not in location)
    except (OSError,TimeoutError):
        return False


def authelia_block():
    return ("    # AUTOKOM AUTH AUTHELIA · session-based login, no Basic popup\n"
            "    location = /internal/autokom/authz {\n"
            "        internal;\n"
            "        proxy_pass http://127.0.0.1:9091/api/authz/auth-request;\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_pass_request_body off;\n"
            "        proxy_set_header Content-Length \"\";\n"
            "        proxy_set_header Connection \"\";\n"
            "        proxy_set_header X-Original-Method $request_method;\n"
            "        proxy_set_header X-Original-URL $scheme://$host$request_uri;\n"
            "        proxy_set_header X-Forwarded-For $remote_addr;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_set_header X-Forwarded-Host $host;\n"
            "    }\n")


def migrate_vhost(content):
    """Return proxy document with fail-closed auth_request and redirect."""
    if 'AUTOKOM AUTH AUTHELIA' in content:
        return content
    if not content.startswith('# AUTOKOM MANAGED') or 'auth_basic_user_file /etc/nginx/.autokom-prometheus.htpasswd;' not in content:
        raise ValueError('Ce vhost n’est pas une configuration Prometheus Basic gérée par AUTOKOM')
    # Auth subrequest is scoped to the HTTPS vhost only, never the HTTP challenge.
    marker='    location / {\n'
    if content.count(marker)!=1:
        raise ValueError('Localisation HTTPS inattendue : migration refusée')
    content=content.replace(marker,authelia_block()+marker,1)
    basic=('        auth_basic "AUTOKOM Metrics";\n'
           '        auth_basic_user_file /etc/nginx/.autokom-prometheus.htpasswd;\n')
    auth=('        auth_request /internal/autokom/authz;\n'
          '        auth_request_set $autokom_login_redirect $upstream_http_location;\n'
          '        error_page 401 =302 $autokom_login_redirect;\n')
    if basic not in content:
        raise ValueError('Auth Basic inconnue, refus d’altérer le vhost')
    return content.replace(basic,auth,1)


def enable_authelia():
    from tools_registry import require_root,ToolError,_tty_challenge
    from tool_files import aliases
    require_root()
    entry=aliases('prometheus').get('nginx-prometheus')
    if not entry:
        raise ToolError('Virtual host Prometheus introuvable ; vérifier autokom tools prometheus files')
    path=entry[0].resolve(strict=True)
    if not path.is_relative_to(Path('/etc/nginx/sites-available')):
        raise ToolError('Nginx Prometheus non géré dans sites-available')
    old=path.read_text(encoding='utf-8')
    match=re.search(r'(?m)^\s*server_name\s+([a-z0-9.-]+);',old)
    if not match:
        raise ToolError('Domaine HTTPS Prometheus introuvable dans le vhost')
    if not authelia_probe(match.group(1)):
        raise ToolError('Authelia non prêt sur 127.0.0.1:9091, ou redirection HTTPS absente : aucune modification. '
                        'Configurer le portail et le endpoint AuthRequest avant de migrer.')
    try: content=migrate_vhost(old)
    except ValueError as exc:raise ToolError(str(exc)) from exc
    if old==content:
        print('  ✓ AuthRequest Authelia déjà présent ; rien à modifier')
        return
    if not _tty_challenge('Activer Authelia pour Prometheus : les anciens identifiants Basic ne fonctionneront plus',
                           'PROMETHEUS'):
        raise ToolError('Opération annulée')
    from observability import owned_write,run
    # Restrict mutation to the vetted, AUTOKOM-owned vhost. Check and restore on error.
    try:
        owned_write(path,content)
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])
    except Exception:
        owned_write(path,old)
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])
        raise
    print('  ✓ Authentification Prometheus via Authelia activée (page de connexion du portail)')
    print('  › Test : connexion refusée sans session, puis redirection HTTPS vers Authelia')
