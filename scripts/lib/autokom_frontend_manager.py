#!/usr/bin/env python3
"""Cycle de vie du frontend statique AUTOKOM (aucun service Node en production).

Mise en production explicite : build non privilégié, contrôle sécurité,
releases versionnées, bascule atomique du symlink, rollback contrôlé.
Ne touche ni aux vhosts backend, ni à BIND9, Cloudflare, Jenkins ou Docker.
"""
from __future__ import annotations

import argparse
import fcntl
from contextlib import contextmanager
import json
import ipaddress
import os
import socket
import time
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / 'platform/frontend'
SECRETS = Path('/etc/autokom/secrets/frontend.env')
STATE = Path('/etc/autokom/frontend-deployment.json')
RELEASES = Path('/opt/autokom/frontend/releases')
CURRENT = RELEASES.parent / 'current'
# Build workspace separated from Django state (/var/lib/autokom, mode 0750).
# Do not put the unprivileged builder under Django's private directory.
# The published React releases remain exclusively in /opt/autokom/frontend.
BUILDS = Path('/var/lib/autokom-frontend/builds')
NPM_CACHE = Path('/var/lib/autokom-frontend/npm-cache')
WORK_ROOT = BUILDS.parent
BUILD_HOME = WORK_ROOT / 'home'
SCANNER_CACHE = WORK_ROOT / 'trivy-cache'
DEPLOY_LOCK = Path('/run/lock/autokom-frontend-deploy.lock')
FRONTEND_LOGS = Path('/var/log/autokom/frontend')
BUILDER = 'autokom-frontend-builder'
VHOST = Path('/etc/nginx/sites-available/autokom-frontend.conf')
MARKER = '# MANAGED BY AUTOKOM FRONTEND'
RULES = ROOT / 'pipelines/autokom-frontend/semgrep-rules.yml'
NODE_PLAYBOOK = ROOT / 'ansible/playbooks/frontend-node.yml'
BUILD_PATH = '/usr/local/bin:/usr/bin:/bin'
MIN_NODE = 'Node.js 20.19+, 22.12+ (Node.js 22 installé par AUTOKOM)'
FQDN = re.compile(r'(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$')
RELEASE_ID = re.compile(r'^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$')


class FrontendError(RuntimeError):
    pass


def domain(value):
    value = (value or '').strip().lower().rstrip('.')
    if not FQDN.fullmatch(value) or value.endswith(('.local', '.example', '.test', '.invalid')):
        raise FrontendError('Domaine public invalide.')
    return value


def run(args, **kw):
    # Pas de capture ni d'affichage des variables de production.
    try:
        subprocess.run(args, check=True, timeout=kw.pop('timeout', 1200), **kw)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        raise FrontendError(f'Échec de {Path(args[0]).name}. Voir les logs de la commande.') from exc


def run_live(args, *, label, cwd=None, env=None, timeout=1200):
    """Keep command output in a private log; show AUTOKOM's existing TTY spinner.

    Never print raw third-party build output on failure (it can contain secrets).
    Commands do not receive a shell, and a timeout always stops the process.
    """
    from tool_banner import Activity
    require_root()
    FRONTEND_LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)
    FRONTEND_LOGS.chmod(0o700)
    log_name = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
    log_path = FRONTEND_LOGS / f'{log_name}-{os.getpid()}-{os.urandom(3).hex()}.log'
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    print(f'  › {label}', flush=True)
    started = time.monotonic()
    try:
        with os.fdopen(fd, 'wb', buffering=0) as log, Activity(f'AUTOKOM · {label}'):
            subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        elapsed = int(time.monotonic() - started)
        print(f'  ✗ {label} ({elapsed}s) · journal protégé : {log_path}', flush=True)
        # AUTOKOM's shared redaction policy prevents tokens or credentials
        # from leaking through third-party diagnostics in the terminal.
        from autokom_app_manager import LiveProgress
        try:
            with log_path.open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 8192))
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
            safe = [LiveProgress._safe_line(line) for line in lines]
            for line in [line for line in safe if line][-3:]:
                print('    │ ' + line[:150], flush=True)
        except OSError:
            pass
        raise FrontendError(f'Échec de {Path(args[0]).name} pendant « {label} ». '
                            f'Consulter le journal protégé {log_path}.') from exc
    print(f'  ✓ {label} ({int(time.monotonic() - started)}s) · journal : {log_path}', flush=True)


def node_compatible(version):
    """Compatibilité Vite 7. La CLI ne dépend d'aucun paquet Python tiers."""
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version.strip())
    if not match:
        return False
    major, minor, _ = (int(n) for n in match.groups())
    return (major == 20 and minor >= 19) or (major == 22 and minor >= 12) or (major >= 24)


def node_toolchain_issue():
    """Contrôle sous le PATH effectivement passé au compte de build.

    Un Node trouvé seulement dans le profil shell root ne suffit pas.
    """
    for executable in ('node', 'npm'):
        if not shutil.which(executable, path=BUILD_PATH):
            return f'{executable} absent du PATH de build ({BUILD_PATH})'
    try:
        result = subprocess.run(
            ['node', '--version'], capture_output=True, text=True, check=True,
            timeout=15, env={'PATH': BUILD_PATH},
        )
        npm = subprocess.run(
            ['npm', '--version'], capture_output=True, text=True, check=True,
            timeout=15, env={'PATH': BUILD_PATH},
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 'Node.js/npm existent mais ne fonctionnent pas'
    version = result.stdout.strip()
    if not node_compatible(version):
        return f'Node.js {version or "inconnu"} incompatible avec Vite 7 ({MIN_NODE})'
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?', npm.stdout.strip()):
        return 'npm ne renvoie pas de version valide'
    return None


def provision_node_toolchain():
    """Provisionnement idempotent, automatique et signé, avant toute écriture Nginx.

    L'installation sur l'hôte n'est lancée que lors de setup --apply.
    La source NodeSource est limitée à la branche 22.x ; aucun script distant
    téléchargé n'est exécuté en shell.
    """
    require_root()
    issue = node_toolchain_issue()
    if issue is None:
        print('  ✓ Node.js/npm compatibles déjà installés : aucune modification APT.')
        return
    if not NODE_PLAYBOOK.is_file():
        raise FrontendError('Playbook de provisioning Node.js absent du dépôt AUTOKOM.')
    if not shutil.which('ansible-playbook'):
        raise FrontendError('Ansible absent : installer la fondation via AUTOKOM avant le frontend.')
    print(f'  › {issue}')
    print('  › AUTOKOM provisionne automatiquement Node.js 22 et npm (APT signé).')
    run_live(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(NODE_PLAYBOOK)],
             label='Provisionnement Node.js/npm via Ansible', cwd=ROOT / 'ansible', timeout=1800)
    issue = node_toolchain_issue()
    if issue:
        raise FrontendError(f'Node.js/npm non opérationnels après provisioning : {issue}')
    print('  ✓ Node.js/npm provisionnés et validés par AUTOKOM.')


def require_root():
    if os.geteuid() != 0:
        raise FrontendError('Cette opération nécessite sudo.')


def read_state():
    if not STATE.is_file():
        if VHOST.is_file() and MARKER in VHOST.read_text(encoding='utf-8'):
            raise FrontendError(
                'Frontend non configuré : setup interrompu après création du vhost. '
                'Vérifier le DNS puis relancer autokom app frontend setup --apply.'
            )
        raise FrontendError('Frontend non configuré. Lancer autokom app frontend setup --check.')
    return json.loads(STATE.read_text(encoding='utf-8'))


def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.frontend-', dir=str(STATE.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.chmod(name, 0o600)
        os.replace(name, STATE)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def config_urls(args):
    from autokom_env_manager import read_env
    env = {} if args.domain and args.backend_domain else read_env(SECRETS)
    front = args.domain or env.get('FRONTEND_URL', '')
    back = args.backend_domain or env.get('BACKEND_URL', '')
    if front.startswith('https://'):
        front = urlsplit(front).hostname or ''
    if back.startswith('https://'):
        back = urlsplit(back).hostname or ''
    return domain(front), domain(back)


def ensure_source():
    required = ['package.json', 'package-lock.json', 'vite.config.js', 'index.html',
                'src/main.jsx', 'src/services/api.js']
    missing = [name for name in required if not (FRONTEND / name).is_file()]
    if missing:
        raise FrontendError('Code frontend incomplet : ' + ', '.join(missing))
    manifest = json.loads((FRONTEND / 'package.json').read_text())
    lock = json.loads((FRONTEND / 'package-lock.json').read_text())
    if manifest.get('name') != lock.get('name') or manifest.get('version') != lock.get('version'):
        raise FrontendError('package.json et package-lock.json divergent.')
    for key in ('dependencies', 'devDependencies'):
        if manifest.get(key, {}) != lock.get('packages', {}).get('', {}).get(key, {}):
            raise FrontendError('package-lock.json doit être régénéré et approuvé.')


def existing_vhost_conflicts(front):
    enabled = Path('/etc/nginx/sites-enabled')
    if not enabled.is_dir():
        raise FrontendError('Nginx sites-enabled manquant.')
    for file in enabled.iterdir():
        if file.name == VHOST.name:
            continue
        try:
            value = file.read_text(encoding='utf-8')
        except OSError:
            continue
        for found in re.findall(r'(?m)^\s*server_name\s+([^;]+);', value):
            if front in found.split():
                raise FrontendError(f'Domaine {front} déjà configuré : {file}. Refus d’écrasement.')


# Ne jamais laisser un vhost SPA entrer en boucle vers /index.html quand
# aucune release n'a encore été publiée (setup HTTPS != deploy React).
NO_RELEASE_MARKER = '# AUTOKOM_NO_RELEASE_GUARD'
NO_RELEASE_LOCATION = '    location / {\n        try_files $uri $uri/ /index.html;'


def nginx_release_guard(config):
    """Ajoute une réponse 503 sûre et idempotente, sans toucher au TLS Certbot.

    La seule forme acceptée est celle du vhost géré par AUTOKOM ; un vhost
    personnalisé doit être révisé plutôt que remplacé aveuglément.
    """
    if MARKER not in config or 'root /opt/autokom/frontend/current;' not in config:
        raise FrontendError('Vhost frontend non géré : refus de migration automatique.')
    if NO_RELEASE_MARKER in config:
        return config
    if config.count(NO_RELEASE_LOCATION) != 1:
        raise FrontendError('Bloc SPA Nginx inattendu : migration automatique refusée.')
    replacement = ("    location / {\n"
                   "        " + NO_RELEASE_MARKER + "\n"
                   "        # La construction initiale n'est pas encore publiée.\n"
                   "        if (!-f /opt/autokom/frontend/current/index.html) {\n"
                   "            return 503 \"AUTOKOM frontend non publie. Lancer autokom app frontend deploy --check puis --apply.\\n\";\n"
                   "        }\n"
                   "        try_files $uri $uri/ /index.html;")
    return config.replace(NO_RELEASE_LOCATION, replacement, 1)


def nginx_config(front, back, port):
    # API Django directement via loopback, et non via une IP Cloudflare.
    return nginx_release_guard(f'''{MARKER}
server {{
    listen 80;
    listen [::]:80;
    server_name {front};
    root /opt/autokom/frontend/current;
    index index.html;
    server_tokens off;
    access_log /var/log/nginx/autokom-frontend.access.log;
    error_log /var/log/nginx/autokom-frontend.error.log warn;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "DENY" always;
    location = /__autokom/health {{ access_log off; add_header Cache-Control "no-store"; return 200 "autokom-frontend-ok\\n"; }}
    # La console frontend n'expose pas le manifeste interne du runner.
    location ^~ /api/internal/ {{ return 404; }}
    location ^~ /api/ {{
        client_max_body_size 16m;
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host {back};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;
        proxy_connect_timeout 10s;
        proxy_read_timeout 90s;
        proxy_redirect off;
        add_header Cache-Control "no-store" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        proxy_no_cache 1;
        proxy_cache_bypass 1;
    }}
    location ~* \\.(?:js|css|webp|png|jpg|jpeg|svg|ico|woff2?)$ {{
        try_files $uri =404;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }}
    location / {{
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-Frame-Options "DENY" always;
        # Les polices Google sont déjà présentes dans les styles actuels.
        add_header Content-Security-Policy "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com" always;
    }}
    location ~ /\\. {{ deny all; }}
}}
''')


def builder_access(flag, path):
    """Check effective rights with the isolated Unix build account (not root)."""
    try:
        result = subprocess.run(
            ['runuser', '-u', BUILDER, '--', '/usr/bin/test', flag, str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FrontendError('Contrôle des permissions du builder indisponible.') from exc
    return result.returncode == 0


def prepare_builder_parent():
    """Check the shared /var/lib directory, without modifying Django's data ACLs.

    WORK_ROOT is a dedicated sibling of /var/lib/autokom, not its child.
    This completely avoids granting a frontend build process access to the
    backend's private parent directory. The build's own workspace is guarded
    separately by builder_prepare (root:builder 0710, private 0700 children).
    """
    parent = WORK_ROOT.parent
    if parent.is_symlink() or not parent.is_dir():
        raise FrontendError('Parent du workspace frontend invalide ou symbolique.')
    if not builder_access('-x', parent):
        raise FrontendError(
            f'{BUILDER} ne peut pas traverser {parent}. '
            'AUTOKOM refuse d’élargir les permissions des répertoires partagés.'
        )


def builder_prepare():
    """Provision private build/cache/home paths without sharing backend secrets."""
    require_root()
    try:
        pwd.getpwnam(BUILDER)
    except KeyError:
        run(['useradd', '--system', '--home-dir', str(BUILD_HOME),
             '--shell', '/usr/sbin/nologin', '--user-group', BUILDER])
    if WORK_ROOT.is_symlink():
        raise FrontendError('Répertoire de travail frontend symbolique non autorisé.')
    # Only a dedicated root-owned workspace is created; never touch Django state.
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    if not WORK_ROOT.is_dir() or WORK_ROOT.stat().st_uid not in (0, pwd.getpwnam(BUILDER).pw_uid):
        raise FrontendError('Propriétaire inattendu pour le répertoire de travail frontend.')
    prepare_builder_parent()
    # Keep all build files private. Group access on the parent is x-only.
    shutil.chown(WORK_ROOT, user='root', group=BUILDER)
    WORK_ROOT.chmod(0o710)
    for folder in (BUILDS, NPM_CACHE, BUILD_HOME, SCANNER_CACHE):
        if folder.is_symlink():
            raise FrontendError('Lien symbolique interdit dans le répertoire de build.')
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir() or folder.stat().st_uid not in (0, pwd.getpwnam(BUILDER).pw_uid):
            raise FrontendError('Propriétaire inattendu dans les dossiers frontend.')
        shutil.chown(folder, user=BUILDER, group=BUILDER)
        folder.chmod(0o700)
    for folder in (WORK_ROOT, BUILDS, NPM_CACHE, BUILD_HOME, SCANNER_CACHE):
        if not builder_access('-x', folder):
            raise FrontendError(
                f'Le compte {BUILDER} ne peut pas traverser {folder}. '
                'Vérifier les ACL de tous les parents.')


def verify_build_access(stage):
    """Validate the entire path *as builder*, before starting any tools."""
    for path, flag, label in (
        (stage / 'package.json', '-r', 'package.json'),
        (stage / 'package-lock.json', '-r', 'package-lock.json'),
        (stage, '-w', 'répertoire de compilation'),
        (NPM_CACHE, '-w', 'cache npm'),
        (BUILD_HOME, '-w', 'HOME du builder'),
        (SCANNER_CACHE, '-w', 'cache des scanners'),
    ):
        if not builder_access(flag, path):
            raise FrontendError(
                f'Accès refusé à {label} pour {BUILDER} ({path}). '
                'Une permission sur un répertoire parent peut manquer.')


@contextmanager
def deployment_guard():
    """One build/release operation at a time, including checks and rollback."""
    require_root()
    DEPLOY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(DEPLOY_LOCK, flags, 0o600)
    except OSError as exc:
        raise FrontendError('Impossible de sécuriser le verrou de publication frontend.') from exc
    try:
        import stat
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o077:
            raise FrontendError('Verrou de publication frontend non sécurisé.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FrontendError('Un autre déploiement AUTOKOM frontend est déjà en cours.') from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def backend_port():
    path = Path('/etc/autokom/app-runtime.json')
    if not path.is_file():
        return 8172
    try:
        value = int(json.loads(path.read_text())['port'])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return 8172
    if not 1024 <= value <= 65535:
        raise FrontendError('Port interne Django invalide.')
    return value


def acme_email(explicit=None):
    """Email facultatif : option CLI > runtime.env > aucune adresse.

    On évite toute obligation de collecter une adresse pour Let's Encrypt.
    """
    value = (explicit or '').strip()
    if not value:
        from autokom_env_manager import read_env
        value = read_env(Path('/etc/autokom/secrets/runtime.env')).get('AUTOKOM_ACME_EMAIL', '').strip()
    if value and (not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value) or len(value) > 254):
        raise FrontendError('Adresse ACME invalide : corriger --email ou AUTOKOM_ACME_EMAIL.')
    return value


def certbot_command(front, email):
    """Arguments non-interactifs, y compris si aucun e-mail n'est fourni."""
    cmd = ['certbot', '--nginx', '--non-interactive', '--agree-tos', '--redirect']
    if email:
        cmd.extend(['--email', email])
    else:
        cmd.append('--register-unsafely-without-email')
    return [*cmd, '-d', front]




def managed_certificate_installed(front, *, live_root=Path('/etc/letsencrypt/live')):
    """Do not re-request an already attached TLS certificate on setup reruns.

    Renewal is still the responsibility of the Certbot systemd timer. A
    missing/changed certificate fails this check and follows the normal setup.
    """
    if not VHOST.is_file():
        return False
    config = VHOST.read_text(encoding='utf-8')
    chain = live_root / front / 'fullchain.pem'
    key = live_root / front / 'privkey.pem'
    return (MARKER in config and f'server_name {front};' in config
            and f'ssl_certificate {chain};' in config
            and f'ssl_certificate_key {key};' in config
            and chain.is_file() and key.is_file())


def cloudflare_frontend_zone(front):
    """Only the public AUTOKOM frontend has automatic DNS write authority.

    All other domains (including infrastructure and customer sites) remain
    managed by their own dedicated AUTOKOM DNS workflows.
    """
    return 'ikom.mg' if front == 'auto.ikom.mg' else None


def frontend_ingress_ip():
    """Get the existing, approved public ingress IPv4; never infer from EC2 metadata."""
    from autokom_env_manager import read_env
    raw = read_env(Path('/etc/autokom/secrets/runtime.env')).get('AUTOKOM_INGRESS_IPV4', '').strip()
    try:
        value = ipaddress.IPv4Address(raw)
    except ipaddress.AddressValueError as exc:
        raise FrontendError('AUTOKOM_INGRESS_IPV4 absent/invalide dans runtime.env.') from exc
    if not value.is_global:
        raise FrontendError('AUTOKOM_INGRESS_IPV4 doit être une IPv4 publique routable.')
    return str(value)


def ensure_frontend_dns(front):
    """Reconcile one DNS hostname using AUTOKOM's *existing* Cloudflare client.

    Never delete records or overwrite an existing target. DNS-only is chosen for
    the initial ACME HTTP-01 validation; proxy policy may be adjusted separately.
    """
    zone = cloudflare_frontend_zone(front)
    if zone is None:
        print('  › DNS externe : AUTOKOM vérifiera sa résolution, sans créer de zone.')
        return None
    ip = frontend_ingress_ip()
    from autokom_dns_cloudflare import Cloudflare, DNSError
    try:
        cf = Cloudflare()
        entries = [r for r in cf.records(record_name=front)
                   if r.get('name', '').rstrip('.').lower() == front]
        routing = [r for r in entries if r.get('type') in ('A', 'AAAA', 'CNAME', 'NS')]
        if not routing:
            cf.create(zone, 'A', front, ip, ttl=120, proxied=False)
            print(f'  ✓ DNS Cloudflare : A {front} créé vers AUTOKOM_INGRESS_IPV4 (DNS only).')
            return ip
        elif (len(routing) == 1 and routing[0].get('type') == 'A'
              and routing[0].get('content') == ip):
            print(f'  ✓ DNS Cloudflare : A {front} déjà conforme, aucune modification.')
            return None if routing[0].get('proxied') else ip
        else:
            raise FrontendError(
                f'DNS {front} déjà occupé par un enregistrement différent ou ambigu. '
                'Refus de modifier un service existant.'
            )
    except DNSError as exc:
        raise FrontendError('DNS Cloudflare : ' + str(exc)) from exc


def dns_ipv4_visible(front, expected_ip=None):
    try:
        addresses = socket.getaddrinfo(front, 80, family=socket.AF_INET, type=socket.SOCK_STREAM)
        return any((ipaddress.ip_address(result[4][0]).is_global and
                    (expected_ip is None or result[4][0] == expected_ip)) for result in addresses)
    except (socket.gaierror, OSError, ValueError):
        return False


def wait_frontend_dns(front, expected_ip=None, timeout=150, interval=5):
    """Résolution avant ACME, avec progression périodique visible à l'écran.

    Le cache DNS peut différer entre l'API Cloudflare et le résolveur local.
    En cas de dépassement du délai, le record est conservé et le setup est
    rejouable sans modifier les autres DNS ni les vhosts.
    """
    started = time.monotonic()
    deadline = started + timeout
    attempt = 0
    print(f'  › Vérification DNS publique de {front} (maximum {timeout}s).', flush=True)
    while True:
        attempt += 1
        print(f'  ⏳ Résolution DNS · tentative {attempt}…', flush=True)
        from tool_banner import Activity
        with Activity(f'AUTOKOM · résolution DNS {front}'):
            visible = dns_ipv4_visible(front, expected_ip)
        if visible:
            print(f'  ✓ DNS public : {front} résolu en IPv4.', flush=True)
            return
        remaining = max(0, deadline - time.monotonic())
        if remaining <= 0:
            raise FrontendError(
                f'DNS de {front} non visible après {int(time.monotonic() - started)}s. '
                'Enregistrement conservé ; relancer le même setup --apply après propagation DNS.'
            )
        print(f'  › Propagation DNS en cours · prochaine vérification dans '
              f'{min(interval, remaining):.0f}s · délai restant {remaining:.0f}s.', flush=True)
        with Activity(f'AUTOKOM · propagation DNS {front}'):
            time.sleep(min(interval, remaining))


def setup(args):
    ensure_source()
    front, back = config_urls(args)
    if front == back:
        raise FrontendError('Les domaines frontend et backend doivent être distincts.')
    email = acme_email(args.email) if args.tls_mode == 'certbot' else ''
    if args.nginx_mode == 'existing' and args.tls_mode == 'certbot':
        raise FrontendError('Avec --nginx-mode existing, les certificats restent gérés par le vhost existant.')
    if args.nginx_mode == 'managed' and args.tls_mode == 'existing':
        if not VHOST.is_file() or ('ssl_certificate' not in VHOST.read_text(encoding='utf-8') and
                                   'listen 443 ssl' not in VHOST.read_text(encoding='utf-8')):
            raise FrontendError('TLS existing nécessite un vhost AUTOKOM HTTPS déjà actif.')
    previous = read_state() if STATE.is_file() else None
    if previous and previous.get('active') and (previous.get('domain') != front or
       previous.get('backend_domain') != back or previous.get('nginx_mode') != args.nginx_mode):
        raise FrontendError('Changement de domaine/mode du site actif : migration manuelle requise.')
    print(f'  Frontend : https://{front} · Backend : https://{back}')
    print(f'  Déploiement : {RELEASES.parent} · Nginx : {args.nginx_mode} · HTTPS : {args.tls_mode}')
    if args.tls_mode == 'certbot' and not email:
        print('  › Certbot : enregistrement sans e-mail (notifications indisponibles).')
    if args.nginx_mode == 'managed':
        existing_vhost_conflicts(front)
        if VHOST.exists() and MARKER not in VHOST.read_text(encoding='utf-8'):
            raise FrontendError('Vhost existant non géré par AUTOKOM : refus de modification.')
        if VHOST.exists() and 'listen 443 ssl' in VHOST.read_text(encoding='utf-8'):
            current = VHOST.read_text(encoding='utf-8')
            if (f'server_name {front};' not in current or
                '/opt/autokom/frontend/current' not in current or
                'location ^~ /api/' not in current):
                raise FrontendError('Vhost TLS géré différent : corriger manuellement avant setup.')
    if not args.apply:
        if args.tls_mode == 'certbot':
            if cloudflare_frontend_zone(front):
                print(f'  › DNS : contrôle/création idempotente du A {front} via Cloudflare avant Certbot.')
            else:
                print(f'  › DNS : résolution publique de {front} exigée avant Certbot.')
        issue = node_toolchain_issue()
        if issue:
            print(f'  › Toolchain : {issue}. Sera provisionnée automatiquement par --apply.')
        else:
            print('  ✓ Toolchain Node.js/npm déjà compatible.')
        print('  ✓ Prévisualisation : aucun fichier de production modifié.')
        return 0
    require_root()
    if not shutil.which('nginx'):
        raise FrontendError('Nginx absent : provisionner la fondation via AUTOKOM install, sans apt manuel.')
    if args.nginx_mode == 'managed' and args.tls_mode == 'certbot' and not shutil.which('certbot'):
        raise FrontendError('Certbot absent : provisionner la fondation via AUTOKOM install.')
    # Ne modifie ni Nginx ni les secrets si le téléchargement/provisionnement échoue.
    provision_node_toolchain()
    if args.tls_mode == 'certbot':
        from tool_banner import Activity
        with Activity('AUTOKOM · vérification DNS Cloudflare'):
            expected_dns_ip = ensure_frontend_dns(front)
        wait_frontend_dns(front, expected_dns_ip)
    builder_prepare()
    RELEASES.mkdir(parents=True, exist_ok=True)
    RELEASES.parent.chmod(0o755)
    RELEASES.chmod(0o755)
    from autokom_env_manager import read_env, write_env, backup, _validate
    values = read_env(SECRETS)
    values.update({'FRONTEND_URL': f'https://{front}', 'BACKEND_URL': f'https://{back}'})
    values.setdefault('VITE_AUTOKOM_API_BASE', '/api')
    values.setdefault('VITE_AUTOKOM_AUTH_MODE', 'cookie')
    values.setdefault('VITE_AUTOKOM_FRONTEND_ONLY', 'false')
    values.setdefault('VITE_AUTOKOM_API_ENABLED', 'true')
    values.setdefault('VITE_AUTOKOM_GITHUB_ENABLED', 'false')  # le sélecteur GitHub est simulé
    _validate('frontend', values)
    if SECRETS.exists():
        backup(SECRETS)
    write_env(SECRETS, values)
    if args.nginx_mode == 'managed':
        VHOST.parent.mkdir(parents=True, exist_ok=True)
        link = Path('/etc/nginx/sites-enabled') / VHOST.name
        original = VHOST.read_text(encoding='utf-8') if VHOST.exists() else None
        # Ne jamais supprimer la configuration HTTPS créée par Certbot.
        if original is None or ('listen 443 ssl' not in original and 'ssl_certificate' not in original):
            new_vhost = nginx_config(front, back, backend_port())
        else:
            # Vhost HTTPS déjà émis par Certbot : préserver certificats et redirects.
            new_vhost = nginx_release_guard(original)
        if original != new_vhost:
            VHOST.write_text(new_vhost, encoding='utf-8')
            VHOST.chmod(0o644)
        created = not link.exists() and not link.is_symlink()
        if created:
            link.symlink_to(VHOST)
        try:
            run(['nginx', '-t'])
            run(['systemctl', 'reload', 'nginx'])
        except FrontendError:
            if original is None:
                VHOST.unlink(missing_ok=True)
            else:
                VHOST.write_text(original, encoding='utf-8')
            if created:
                link.unlink(missing_ok=True)
            raise
        if args.tls_mode == 'certbot':
            if managed_certificate_installed(front):
                print('  ✓ Certificat TLS déjà installé : aucune nouvelle demande ACME.')
            else:
                run_live(certbot_command(front, email), label='Certificat HTTPS Let’s Encrypt', timeout=300)
    save_state({'domain': front, 'backend_domain': back, 'nginx_mode': args.nginx_mode,
                'tls_mode': args.tls_mode, 'releases': previous.get('releases', []) if previous else [],
                'active': previous.get('active') if previous else None})
    print('  ✓ Configuration frontend installée. Effectuer le deploy --check avant --apply.')
    if not (previous and previous.get('active')):
        print('  › Première release React non publiée : aucune application statique à servir pour le moment.')
    return 0


def build_environment(source):
    # Seules des variables publiques explicitement allowlistées sont transmises à Vite.
    from autokom_env_manager import read_env
    values = read_env(SECRETS)
    allowed = {'VITE_AUTOKOM_FRONTEND_ONLY': {'true', 'false'},
               'VITE_AUTOKOM_API_ENABLED': {'true', 'false'},
               'VITE_AUTOKOM_GITHUB_ENABLED': {'true', 'false'},
               'VITE_AUTOKOM_API_BASE': {'/api'},
               'VITE_AUTOKOM_AUTH_MODE': {'cookie', 'bearer'}}
    public = {}
    for key, accepted in allowed.items():
        defaults = {'VITE_AUTOKOM_API_BASE': '/api', 'VITE_AUTOKOM_AUTH_MODE': 'cookie'}
        value = values.get(key, defaults.get(key, 'false'))
        if value not in accepted:
            raise FrontendError('Configuration Vite invalide : '+key)
        public[key] = value
    # Pour l'instant GitHub est un composant de démonstration : ne pas l'annoncer connecté.
    if public['VITE_AUTOKOM_GITHUB_ENABLED'] != 'false':
        raise FrontendError('L’UI GitHub simulée doit rester désactivée jusqu’à la liaison OAuth réelle.')
    env = {'HOME': str(BUILD_HOME), 'PATH': BUILD_PATH,
           'XDG_CACHE_HOME': str(BUILD_HOME / '.cache'),
           'XDG_CONFIG_HOME': str(BUILD_HOME / '.config'),
           'XDG_DATA_HOME': str(BUILD_HOME / '.local/share'),
           'TRIVY_CACHE_DIR': str(SCANNER_CACHE),
           'NPM_CONFIG_CACHE': str(NPM_CACHE), 'CI': 'true', **public}
    return env


def copy_source(directory):
    directory.mkdir()
    for name in ('package.json', 'package-lock.json', 'vite.config.js', 'index.html'):
        shutil.copy2(FRONTEND / name, directory / name)
    for name in ('src', 'public', 'tests'):
        shutil.copytree(FRONTEND / name, directory / name)
    shutil.copy2(RULES, directory / '.semgrep-rules.yml')


def chown_recursive(folder):
    shutil.chown(folder, BUILDER, BUILDER)
    for directory, dirs, files in os.walk(folder):
        for item in dirs + files:
            shutil.chown(Path(directory) / item, BUILDER, BUILDER)


def builder_run(command, cwd, env, *, label=None):
    run_live(['runuser', '-u', BUILDER, '--', '/usr/bin/env', '-i',
              *(f'{key}={value}' for key, value in env.items()), *command],
             cwd=cwd, label=label or f'Frontend · {command[0]}', timeout=1800)


def deploy(args):
    with deployment_guard():
        return deploy_locked(args)


def deploy_locked(args):
    require_root()
    ensure_source()
    state = read_state()
    issue = node_toolchain_issue()
    if issue:
        raise FrontendError(f'{issue}. Relancer autokom app frontend setup --apply pour auto-provisionner.')
    for tool in ('semgrep', 'trivy'):
        if not shutil.which(tool):
            raise FrontendError(f'{tool} absent : bloqué (scans DevSecOps obligatoires).')
    if not RULES.is_file():
        raise FrontendError('Règles Semgrep frontend manquantes.')
    builder_prepare()
    env = build_environment(FRONTEND)
    if env['VITE_AUTOKOM_FRONTEND_ONLY'] != 'false' or env['VITE_AUTOKOM_API_ENABLED'] != 'true':
        print('  › Mode simulation/API désactivée configuré : l’interface demeure en démonstration.')
    # Une copie de travail isolée évite npm dans le checkout Git ou sur le vhost.
    with tempfile.TemporaryDirectory(prefix='build-', dir=BUILDS) as temp:
        stage = Path(temp) / 'source'
        copy_source(stage)
        chown_recursive(Path(temp))
        stage.chmod(0o700)
        verify_build_access(stage)
        print('  [1/6] Dépendances npm immuables, scripts tiers désactivés')
        builder_run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], stage, env, label='Dépendances npm reproductibles')
        print('  [2/6] Tests frontend')
        builder_run(['npm', 'run', 'test'], stage, env, label='Tests React')
        print('  [3/6] Audit dépendances (HIGH/CRITICAL bloquants)')
        builder_run(['npm', 'audit', '--audit-level=high'], stage, env, label='Audit des dépendances')
        print('  [4/6] SAST Semgrep')
        builder_run(['semgrep', 'scan', '--config', '.semgrep-rules.yml', '--error', '--metrics', 'off', 'src'], stage, env, label='Analyse Semgrep')
        print('  [5/6] Analyse Trivy (vulnérabilités + secrets)')
        builder_run(['trivy', 'fs', '--scanners', 'vuln,secret', '--exit-code', '1',
                     '--severity', 'HIGH,CRITICAL', '--no-progress', '.'], stage, env, label='Analyse Trivy')
        print('  [6/6] Compilation Vite, sans sourcemap publique')
        builder_run(['npm', 'run', 'build'], stage, env, label='Compilation Vite')
        dist = stage / 'dist'
        if not (dist / 'index.html').is_file() or not (dist / 'assets').is_dir():
            raise FrontendError('Build Vite incomplet : aucun artefact publiable.')
        if args.check:
            print('  ✓ Vérification terminée : aucun release publié, aucun Nginx modifié.')
            return 0
        if state['nginx_mode'] == 'managed':
            run(['nginx', '-t'])
        release_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+os.urandom(4).hex()
        release = RELEASES / release_id
        if release.exists():
            raise FrontendError('Identifiant de release déjà utilisé.')
        tmp = RELEASES / ('.'+release_id+'.incoming')
        shutil.copytree(dist, tmp)
        for base, dirs, files in os.walk(tmp):
            for filename in files:
                (Path(base) / filename).chmod(0o644)
            for dirname in dirs:
                (Path(base) / dirname).chmod(0o755)
        tmp.chmod(0o755)
        os.replace(tmp, release)
        try:
            activate(release, state)
        except Exception:
            # Une release orpheline peut être supprimée ultérieurement, pas de destruction auto.
            raise
        state['releases'] = list(dict.fromkeys([release_id, *state.get('releases', [])]))
        state['active'] = release_id
        save_state(state)
        print('  ✓ Frontend publié :', release_id)
    return 0


def activate(release, state):
    if not release.is_dir() or release.parent != RELEASES or not RELEASE_ID.fullmatch(release.name):
        raise FrontendError('Release non autorisée.')
    if not (release / 'index.html').is_file():
        raise FrontendError('Release invalide (index.html manquant).')
    old = CURRENT.resolve(strict=True) if CURRENT.is_symlink() and CURRENT.exists() else None
    if CURRENT.exists() and not CURRENT.is_symlink():
        raise FrontendError('Le chemin current existe sans être un lien symbolique : refus.')
    link = CURRENT.parent / '.current-next'
    if link.exists() or link.is_symlink():
        raise FrontendError('Bascule déjà en cours : lien temporaire présent.')
    link.symlink_to(release)
    os.replace(link, CURRENT)
    try:
        if state['nginx_mode'] == 'managed':
            run(['nginx', '-t'])
            run(['systemctl', 'reload', 'nginx'])
    except Exception:
        if old and old.is_dir():
            restore = CURRENT.parent / '.current-restore'
            restore.symlink_to(old)
            os.replace(restore, CURRENT)
        else:
            CURRENT.unlink(missing_ok=True)
        raise


def rollback(args):
    with deployment_guard():
        return rollback_locked(args)


def rollback_locked(args):
    require_root()
    state = read_state()
    releases = state.get('releases', [])
    active = state.get('active')
    targets = [item for item in releases if item != active]
    requested = args.to or (targets[0] if targets else None)
    if not requested or not RELEASE_ID.fullmatch(requested) or requested not in releases:
        raise FrontendError('Aucune version précédente autorisée. Utiliser --to RELEASE_ID.')
    print(f'  Rollback : {active} -> {requested}')
    if not args.apply:
        print('  ✓ Prévisualisation, aucune modification effectuée.')
        return 0
    activate(RELEASES / requested, state)
    state['active'] = requested
    state['releases'] = [requested] + [r for r in releases if r != requested]
    save_state(state)
    print('  ✓ Version restaurée.')
    return 0


def status(args):
    state = read_state()
    active = state.get('active')
    print('  Domaine : https://' + state['domain'])
    print('  Backend : https://' + state['backend_domain'])
    print('  Release active : ' + (active or 'aucune'))
    valid = bool(active and CURRENT.is_symlink() and CURRENT.resolve() == RELEASES / active
                 and (RELEASES / active / 'index.html').is_file())
    print('  Node.js/npm : ' + ('OK' if node_toolchain_issue() is None else 'À PROVISIONNER via setup --apply'))
    print('  Artefact et symlink : ' + ('OK' if valid else 'ABSENT/INCOHÉRENT'))
    if not active and not CURRENT.is_symlink():
        print('  › Configuration prête ; aucune release React publiée pour le moment.')
        print('  › Étapes : autokom app frontend deploy --check puis --apply.')
    return 0 if valid else 1


def logs(args):
    require_root()
    path = Path('/var/log/nginx/autokom-frontend.access.log' if args.source == 'access'
                else '/var/log/nginx/autokom-frontend.error.log')
    if not path.is_file():
        raise FrontendError('Aucun journal Nginx frontend disponible pour ce type.')
    run(['tail', '-n', str(args.lines), str(path)], timeout=20)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='autokom app frontend')
    sub = parser.add_subparsers(dest='command', required=True)
    setup_p = sub.add_parser('setup', help='Préparer le frontend, le domaine, le reverse proxy et HTTPS')
    setup_p.add_argument('--domain', help='Domaine frontend, ex. auto.ikom.mg')
    setup_p.add_argument('--backend-domain', help='Domaine Django, ex. backend.auto.ikom.mg')
    setup_p.add_argument('--nginx-mode', choices=('managed', 'existing'), default='managed')
    setup_p.add_argument('--tls-mode', choices=('certbot', 'existing'), default='certbot')
    setup_p.add_argument('--email', help='Email ACME facultatif (sinon runtime.env, ou aucun)')
    setup_p.add_argument('--check', action='store_true', help='Prévisualiser sans modifier')
    setup_p.add_argument('--apply', action='store_true', help='Effectuer explicitement le setup')
    deploy_p = sub.add_parser('deploy', help='Tests, scans, build Vite, publication atomique')
    deploy_p.add_argument('--check', action='store_true', help='Tester et compiler sans publier')
    deploy_p.add_argument('--apply', action='store_true', help='Publier explicitement')
    rollback_p = sub.add_parser('rollback', help='Revenir à une release précédente')
    rollback_p.add_argument('--to', help='Identifiant exact de release')
    rollback_p.add_argument('--check', action='store_true')
    rollback_p.add_argument('--apply', action='store_true')
    sub.add_parser('status', help='Domaine, release active et intégrité des liens')
    logs_p = sub.add_parser('logs', help='Journaux Nginx de ce frontend')
    logs_p.add_argument('--source', choices=('access', 'error'), default='error')
    logs_p.add_argument('--lines', type=int, default=80)
    args = parser.parse_args(argv)
    if args.command == 'logs' and not 1 <= args.lines <= 1000:
        raise FrontendError('Nombre de lignes attendu entre 1 et 1000.')
    if args.command == 'setup':
        if args.apply and args.check:
            raise FrontendError('Choisir soit --check soit --apply.')
        return setup(args)
    if args.command == 'deploy':
        if args.apply == args.check:
            raise FrontendError('Préciser soit --check soit --apply.')
        return deploy(args)
    if args.command == 'rollback':
        if args.apply and args.check:
            raise FrontendError('Choisir soit --check soit --apply.')
        return rollback(args)
    if args.command == 'logs': return logs(args)
    return status(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except FrontendError as exc:
        print('AUTOKOM Frontend : ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
