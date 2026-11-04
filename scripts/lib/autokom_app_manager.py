#!/usr/bin/env python3
"""AUTOKOM Platform backend lifecycle (systemd by default, Docker optional).

Root privileges are used only in explicit setup/deploy operations, never by the
Django application process. Does not install arbitrary client applications.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import time
import getpass
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'platform' / 'backend'
CONFIG_DIR = Path('/etc/autokom')
ENV_FILE = CONFIG_DIR / 'secrets' / 'backend.env'
STATE_FILE = CONFIG_DIR / 'app-runtime.json'
SOURCE_ENV_CANDIDATES = (ENV_FILE,)
ANSIBLE_PLAYBOOK = ROOT / 'ansible/playbooks/platform-backend.yml'
USER = 'autokom'
SERVICE = 'autokom-backend'
VENV = Path('/opt/autokom/venv')
PORT = 8172
SECURITY_RULES = ROOT / 'pipelines' / 'autokom-backend' / 'semgrep-rules.yml'
OPERATION_LOGS = Path('/var/log/autokom/platform')
_LIVE = None
_STAGE = None


class AppError(RuntimeError):
    pass


class LiveProgress:
    """Transitory spinner + three green lines; full logs remain protected on disk.

    Only non-sensitive, sanitized log text reaches the live TTY preview.
    Terminal state is restored at each phase boundary, including failures.
    """

    FRAMES = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
    SENSITIVE = re.compile(
        r'(?i)(password|passwd|secret|token|authorization|credential|'
        r'private[_ -]?key|api[_ -]?key|access[_ -]?key|cookie|bearer|'
        r'jwt|smtp|mysql://|postgres://|https?://[^/\s:@]+:[^/\s@]+@)'
    )

    def __init__(self, action):
        self.action = action
        self.log_path = None
        self._frame = 0
        self._last = 0.0
        self._tty = sys.stdout.isatty()
        self._visible = False
        self._preview_start = 0

    def __enter__(self):
        OPERATION_LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(OPERATION_LOGS, 0o700)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        path = OPERATION_LOGS / f'{self.action}-{stamp}-{os.getpid()}.log'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(f'AUTOKOM · {self.action} · {datetime.now().isoformat(timespec="seconds")}\n')
        self.log_path = path
        global _LIVE
        if _LIVE is not None:
            raise AppError('Une opération AUTOKOM est déjà en cours.')
        _LIVE = self
        print(f'  ◈ AUTOKOM · {self.action.upper()}')
        return self

    def __exit__(self, exc_type, exc, tb):
        global _LIVE
        self.clear()
        _LIVE = None
        if exc_type:
            print(f'  ✗ Opération interrompue · diagnostic : {self.log_path}')
        else:
            print(f'  ✓ Journal détaillé : {self.log_path}')

    def write(self, value):
        with self.log_path.open('a', encoding='utf-8') as handle:
            handle.write(value)

    def start_preview(self):
        """Do not mix output from previous phases into the current preview."""
        self._preview_start = self.log_path.stat().st_size

    @classmethod
    def _safe_line(cls, value):
        # Discard ANSI/control sequences from subprocess output (terminal safety).
        value = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', value)
        value = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', value).strip()
        if not value:
            return ''
        if cls.SENSITIVE.search(value):
            return 'sortie masquée (information potentiellement sensible)'
        return value

    def _last_lines(self):
        if not self.log_path:
            return []
        try:
            with self.log_path.open('rb') as handle:
                handle.seek(0, os.SEEK_END)
                end = handle.tell()
                handle.seek(max(self._preview_start, end - 16384))
                raw = handle.read().decode('utf-8', errors='replace')
            cleaned = [self._safe_line(line) for line in raw.splitlines()]
            return [line for line in cleaned if line][-3:]
        except OSError:
            return []

    def paint(self, label, elapsed):
        if not self._tty:
            return
        now = time.monotonic()
        if now - self._last < 0.18:
            return
        self._last = now
        width = max(26, shutil.get_terminal_size((90, 24)).columns)
        frame = self.FRAMES[self._frame % len(self.FRAMES)]
        self._frame += 1
        summary = f'  {frame} {label} · {int(elapsed)}s'[:width - 1]
        lines = self._last_lines() or ['Processus actif ; aucune sortie reçue à cet instant']
        lines = [''] * (3-len(lines)) + lines[-3:]
        if self._visible:
            sys.stdout.write('\r\x1b[3A')
        rendered = [summary] + [f'\x1b[32m  │ {v[:max(1,width-6)]}\x1b[0m' for v in lines]
        sys.stdout.write('\r\x1b[2K' + ('\n\r\x1b[2K'.join(rendered)))
        sys.stdout.flush()
        self._visible = True

    def clear(self):
        if self._tty and self._visible:
            sys.stdout.write('\r\x1b[3A')
            for i in range(4):
                sys.stdout.write('\r\x1b[2K')
                if i < 3:
                    sys.stdout.write('\n')
            sys.stdout.write('\r\x1b[3A')
            sys.stdout.flush()
            self._visible = False


@contextmanager
def operation(action):
    with LiveProgress(action) as progress:
        yield progress


@contextmanager
def phase(label):
    global _STAGE
    old = _STAGE
    _STAGE = label
    start = time.monotonic()
    if _LIVE:
        _LIVE.paint(label, 0)
    try:
        yield
    except Exception:
        if _LIVE:
            _LIVE.clear()
        print(f'  ✗ {label} ({int(time.monotonic() - start)}s)')
        if _LIVE:
            # Database exceptions can include sensitive stack traces; use a
            # concise reason rather than printing arbitrary traceback lines.
            if label.startswith('MySQL'):
                print('    │ Authentification ou connexion refusée · assistance disponible')
            else:
                recent = _LIVE._last_lines()
                for line in recent[-3:]:
                    print(f'    \x1b[32m│ {line[:170]}\x1b[0m' if sys.stdout.isatty() else f'    │ {line[:170]}')
        raise
    else:
        if _LIVE:
            _LIVE.clear()
        print(f'  ✓ {label} ({int(time.monotonic() - start)}s)')
    finally:
        _STAGE = old


def call(args, *, cwd=None, input_text=None, timeout=120, env=None):
    """Run synchronously with live TTY progress while redirecting verbose output.

    Exit codes and timeouts always fail closed. Never include SQL/stdin secrets
    or full command arguments in the log or the user-facing error.
    """
    if _LIVE is None:
        try:
            return subprocess.run(args, cwd=cwd, input=input_text, text=True,
                                  timeout=timeout, check=True, env=env)
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
            raise AppError(f'Échec de {args[0]} ({getattr(exc, "returncode", "interrompu")}).') from exc

    label = _STAGE or Path(args[0]).name
    _LIVE.write(f'\n[{datetime.now().isoformat(timespec="seconds")}] {label} · {Path(args[0]).name}\n')
    _LIVE.start_preview()
    start = time.monotonic()
    try:
        with _LIVE.log_path.open('ab', buffering=0) as log:
            proc = subprocess.Popen(args, cwd=cwd, env=env,
                                    stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT)
            if input_text is not None:
                proc.stdin.write(input_text.encode('utf-8'))
                proc.stdin.close()
            while proc.poll() is None:
                if time.monotonic() - start > timeout:
                    proc.kill()
                    proc.wait()
                    raise AppError(f'Délai dépassé : {label}. Journal : {_LIVE.log_path}')
                _LIVE.paint(label, time.monotonic() - start)
                time.sleep(0.12)
            _LIVE.clear()
            if proc.returncode:
                raise AppError(f'Échec : {label} (code {proc.returncode}). Journal : {_LIVE.log_path}')
            return subprocess.CompletedProcess(args, proc.returncode)
    except OSError as exc:
        raise AppError(f'Impossible de lancer : {label}. Journal : {_LIVE.log_path}') from exc


def root_only():
    if os.geteuid():
        raise AppError('Commande administrative : utiliser sudo autokom app ...')


def validate_domain(value):
    value = (value or '').strip().lower().rstrip('.')
    if not 4 <= len(value) <= 253 or not re.fullmatch(
        r'(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+', value
    ) or value.endswith(('.local', '.invalid', '.test', '.example')):
        raise AppError('Domaine public FQDN invalide (ex. api.autokom.example.org).')
    return value


def load_dotenv(path):
    result = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if any(c in value for c in '\r\n\x00'):
            raise AppError('Valeur .env contenant des caractères interdits.')
        result[key] = value
    return result


ALLOWED_ENV_KEYS = {
    'DEBUG', 'DJANGO_SECRET_KEY', 'CLIENT_JWT_SIGNING_KEY',
    'CLIENT_JWT_ISSUER', 'CLIENT_JWT_AUDIENCE', 'CLIENT_ACCESS_MINUTES',
    'CLIENT_REFRESH_DAYS', 'DB_TEST_NAME', 'EMAIL_USE_SSL',
    'AES_KEY', 'AES_IV', 'AUTOKOM_GUNICORN_WORKERS', 'AUTOKOM_ENV_NAME',
    'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI',
    'GOOGLE_ALLOWED_REDIRECT_URIS', 'GOOGLE_CLIENT_SECRET_FILE',
    'DB_ENGINE', 'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
    'ALLOWED_HOSTS', 'FRONTEND_URL', 'CSRF_TRUSTED_ORIGINS', 'CORS_ALLOWED_ORIGINS',
    'EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS', 'EMAIL_HOST_USER',
    'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL', 'GITHUB_APP_CLIENT_ID',
    'GITHUB_APP_CLIENT_SECRET', 'GITHUB_APP_SLUG', 'GITHUB_APP_CALLBACK_URL',
    'GITHUB_WEBHOOK_SECRET', 'GITHUB_APP_ID', 'GITHUB_APP_PRIVATE_KEY_PATH',
    'AUTOKOM_FIELD_ENCRYPTION_KEY', 'AUTOKOM_BASE_DOMAIN', 'AUTOKOM_MANAGED_DOMAIN_SUFFIX',
    'AUTOKOM_RUNNER_SHARED_KEY', 'GITHUB_API_VERSION',
    'AUTOKOM_STATIC_ROOT', 'AUTOKOM_MEDIA_ROOT', 'TRUST_PROXY_SSL_HEADER',
    'SECURE_SSL_REDIRECT', 'SECURE_HSTS_SECONDS', 'AUTOKOM_SERVE_STATIC',
}


def secure_env_write(values):
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ENV_FILE.parent, 0o700)
    # Never copy arbitrary .env keys to systemd. Explicit whitelist prevents
    # accidental EnvironmentFile injection and stale/wrong runtime settings.
    allowed = ALLOWED_ENV_KEYS
    output = []
    for key in sorted(set(values) & allowed):
        val = str(values[key])
        if any(ch in val for ch in ('\n', '\r', '\x00')):
            raise AppError('Valeur d’environnement invalide : ' + key)
        # Values use systemd EnvironmentFile double-quoted escaping.
        escaped = val.replace('\\', '\\\\').replace('"', '\\"')
        output.append(f'{key}="{escaped}"')
    # Explicitly report unknown variables instead of silently dropping configuration.
    unknown = sorted(set(values).difference(allowed))
    if unknown:
        raise AppError('Variables .env non reconnues (aucune suppression silencieuse) : ' + ', '.join(unknown))
    tmp = ENV_FILE.with_suffix('.new')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(output) + '\n')
        # systemd reads EnvironmentFile as manager; run_as_service injects the
        # environment after privilege drop. No direct read by unprivileged users.
        shutil.chown(tmp, 'root', 'root')
        os.chmod(tmp, 0o600)
        os.replace(tmp, ENV_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()


def create_user():
    try:
        account = pwd.getpwnam(USER)
        if account.pw_uid == 0:
            raise AppError('Compte autokom invalide : UID root.')
    except KeyError:
        call(['useradd', '--system', '--user-group', '--home-dir', '/var/lib/autokom',
              '--shell', '/usr/sbin/nologin', USER])
    for directory in ('/var/lib/autokom', '/var/lib/autokom/media', '/var/lib/autokom/staticfiles', '/var/log/autokom'):
        Path(directory).mkdir(parents=True, exist_ok=True)
        shutil.chown(directory, USER, USER)
        os.chmod(directory, 0o750)
    # Repository content stays owned by its original owner. The service is
    # not granted repository-wide write access or elevated privileges.
    if '/home/' in str(ROOT):
        if not shutil.which('setfacl'):
            raise AppError('Le dépôt est dans /home ; installer le paquet acl (setfacl) pour accorder uniquement le droit de traversée.')
        for ancestor in reversed(list(ROOT.parents)):
            if ancestor in (Path('/'), Path('/home')):
                continue
            if str(ancestor).startswith('/home/'):
                call(['setfacl', '-m', 'u:autokom:--x', str(ancestor)])
    # Allow nginx to traverse only the static directory, never the secret files.
    if shutil.which('setfacl'):
        call(['setfacl', '-m', 'u:www-data:--x', '/var/lib/autokom'])
        call(['setfacl', '-R', '-m', 'u:www-data:r-X', '/var/lib/autokom/staticfiles'])
        call(['setfacl', '-d', '-m', 'u:www-data:r-X', '/var/lib/autokom/staticfiles'])
    # Read-only ACLs to source only. Never expose .env, .git or private keys
    # via a recursive unrestricted permission grant.
    if shutil.which('setfacl'):
        for directory in (ROOT, ROOT / 'platform', BACKEND):
            call(['setfacl', '-m', 'u:autokom:r-x', str(directory)])
        for source in BACKEND.rglob('*'):
            relative = source.relative_to(BACKEND)
            is_django_secret_app = relative.parts[:3] == ('apps', 'projects', 'secrets')
            excluded = {'.git', '.venv', '__pycache__', '.env'}
            if not is_django_secret_app:
                excluded.add('secrets')
            if source.is_symlink() or any(piece in excluded for piece in relative.parts):
                continue
            if source.is_dir():
                call(['setfacl', '-m', 'u:autokom:r-x', str(source)])
            elif source.is_file():
                call(['setfacl', '-m', 'u:autokom:r--', str(source)])
    if (BACKEND / '.env').is_file():
        os.chmod(BACKEND / '.env', 0o600)
    # The service's writable workspaces live outside the Git checkout.
    for folder in ('working', 'artifacts'):
        path = Path('/var/lib/autokom') / folder
        path.mkdir(parents=True, exist_ok=True)
        shutil.chown(path, USER, USER)
        os.chmod(path, 0o750)


def provision_database(values):
    if not shutil.which('mysql'):
        raise AppError('Client mysql manquant ; installer mysql-client avant --provision-db.')
    db = values.get('DB_NAME', 'autokom_platform')
    user = values.get('DB_USER', 'autokom_platform')
    if not (re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,31}', db) and
            re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,31}', user)):
        raise AppError('Nom de base ou utilisateur MySQL invalide.')
    # Provisioning is intentionally non-destructive. Existing DB requires
    # explicit manual migration and credential management.
    out = subprocess.run(['mysql', '--protocol=socket', '-u', 'root', '--batch', '--skip-column-names'],
                         input=f"SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='{db}';\n",
                         text=True, capture_output=True, check=False, timeout=15)
    if out.returncode:
        raise AppError('Authentification MySQL root par socket impossible. Configurer .env manuellement.')
    if out.stdout.strip():
        raise AppError('La base existe déjà ; --provision-db refuse de modifier les données existantes.')
    existing_user = subprocess.run(['mysql', '--protocol=socket', '-u', 'root', '--batch', '--skip-column-names'],
                                  input=f"SELECT User FROM mysql.user WHERE User='{user}' AND Host IN ('localhost','127.0.0.1') LIMIT 1;\n",
                                  text=True, capture_output=True, timeout=15, check=False)
    if existing_user.returncode or existing_user.stdout.strip():
        raise AppError('Utilisateur MySQL préexistant : attribution/modification automatique refusée.')
    password = secrets.token_urlsafe(36)
    # token_urlsafe contains only A-Z a-z 0-9 _ - and needs no SQL escaping.
    sql = (f'CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n'
           f"CREATE USER '{user}'@'localhost' IDENTIFIED BY '{password}';\n"
           f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY '{password}';\n"
           f"GRANT ALL PRIVILEGES ON `{db}`.* TO '{user}'@'localhost';\n"
           f"GRANT ALL PRIVILEGES ON `{db}`.* TO '{user}'@'127.0.0.1';\n")
    call(['mysql', '--protocol=socket', '-u', 'root'], input_text=sql, timeout=30)
    values.update(DB_ENGINE='mysql', DB_NAME=db, DB_USER=user, DB_PASSWORD=password, DB_HOST='127.0.0.1', DB_PORT='3306')


def install_python():
    if not shutil.which('python3'):
        raise AppError('Python 3 requis.')
    if not VENV.exists():
        VENV.parent.mkdir(parents=True, exist_ok=True)
        call(['python3', '-m', 'venv', str(VENV)], timeout=120)
    call([str(VENV / 'bin/pip'), 'install', '--disable-pip-version-check',
          '-r', str(BACKEND / 'requirements.txt'), 'gunicorn>=22,<24'], timeout=900)
    # Guard against compromise by the service account.
    call(['chown', '-R', 'root:root', str(VENV)], timeout=180)
    call(['chmod', '-R', 'go-w', str(VENV)], timeout=180)


def write_service(port=PORT, workers=2):
    unit = f'''[Unit]
Description=AUTOKOM Platform Django Backend
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User={USER}
Group={USER}
WorkingDirectory={BACKEND}
EnvironmentFile={ENV_FILE}
ExecStart={VENV}/bin/gunicorn config.wsgi:application --bind 127.0.0.1:{port} --workers {workers} --timeout 90 --access-logfile - --error-logfile -
Restart=on-failure
RestartSec=5
UMask=0027
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/autokom /var/log/autokom
ProtectControlGroups=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
'''
    dest = Path('/etc/systemd/system/autokom-backend.service')
    dest.write_text(unit, encoding='utf-8')
    os.chmod(dest, 0o644)
    call(['systemctl', 'daemon-reload'])
    call(['systemctl', 'enable', SERVICE])


def write_nginx(domain, email, *, issue_tls, runtime, port=PORT):
    if not shutil.which('nginx'):
        raise AppError('Nginx absent ; exécuter autokom install avant app setup.')
    avail = Path('/etc/nginx/sites-available')
    enabled = Path('/etc/nginx/sites-enabled')
    if not avail.is_dir() or not enabled.is_dir():
        raise AppError('Layout Nginx sites-available/sites-enabled introuvable.')
    target = avail / 'autokom-platform.conf'
    if target.exists() and '# MANAGED BY AUTOKOM PLATFORM' not in target.read_text(encoding='utf-8'):
        raise AppError('Le vhost autokom-platform.conf existe sans marqueur AUTOKOM ; écrasement interdit.')
    if target.exists():
        current = target.read_text(encoding='utf-8')
        if 'server_name ' + domain + ';' not in current:
            raise AppError('Le domaine du vhost AUTOKOM existant est différent : reconfiguration manuelle nécessaire.')
        if 'ssl_certificate ' in current or 'listen 443 ssl' in current:
            # Certbot already modified this file; don't overwrite its TLS configuration.
            call(['nginx', '-t'])
            print('  Configuration Nginx/HTTPS existante conservée.')
            return
    # Reject collisions with other existing virtual hosts.
    for config in enabled.iterdir():
        if config.name == target.name:
            continue
        try:
            text = config.read_text(encoding='utf-8')
        except OSError:
            continue
        for match in re.finditer(r'(?m)^\s*server_name\s+([^;]+);', text):
            if domain in match.group(1).split():
                raise AppError(f'Domaine déjà pris par un autre vhost : {config}.')
    static_location = ('location /static/ { alias /var/lib/autokom/staticfiles/; autoindex off; }'
                       if runtime == 'systemd' else
                       f'location /static/ {{ proxy_pass http://127.0.0.1:{port}; }}')
    conf = f'''# MANAGED BY AUTOKOM PLATFORM
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    client_max_body_size 20m;
    {static_location}
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 15s;
        proxy_read_timeout 90s;
    }}
}}
'''
    # Verify prospective configuration, and roll back if nginx validation fails.
    original = target.read_text(encoding='utf-8') if target.exists() else None
    target.write_text(conf, encoding='utf-8')
    target.chmod(0o644)
    link = enabled / target.name
    created_link = not link.exists() and not link.is_symlink()
    if created_link:
        link.symlink_to(target)
    try:
        call(['nginx', '-t'])
        call(['systemctl', 'reload', 'nginx'])
    except AppError:
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(original, encoding='utf-8')
        if created_link:
            link.unlink(missing_ok=True)
        raise
    if issue_tls:
        if not shutil.which('certbot'):
            raise AppError('Certbot absent. Le vhost HTTP existe ; installer certbot puis relancer setup.')
        call(['certbot', '--nginx', '--non-interactive', '--agree-tos', '--redirect',
              '--email', email, '-d', domain], timeout=240)


def load_state():
    if not STATE_FILE.is_file():
        raise AppError('Backend non configuré. Lancer sudo autokom app setup ...')
    return json.loads(STATE_FILE.read_text(encoding='utf-8'))


def django_env():
    if not ENV_FILE.is_file():
        raise AppError('Configuration /etc/autokom/secrets/backend.env manquante.')
    data = load_dotenv(ENV_FILE)
    env = dict(os.environ)
    env.update(data)
    return env


def run_as_service(args, *, timeout=300):
    env = django_env()
    # Explicitly drop privileges to the system user for Django operations.
    command = ['runuser', '-u', USER, '--', *args]
    return call(command, cwd=BACKEND, env=env, timeout=timeout)


def ensure_django_secrets_package_access():
    """Grant read-only access to the *Django source package*, not to stored credentials.

    This is intentionally repeatable: setup may have run before Git tracked the
    application. Do not open /etc/autokom/secrets or arbitrary directories.
    """
    package = BACKEND / 'apps' / 'projects' / 'secrets'
    if not (package / '__init__.py').is_file() or not (package / 'apps.py').is_file():
        raise AppError('Application Django apps.projects.secrets absente du dépôt. '
                       'Vérifier son suivi Git et exécuter autokom update.')
    paths = [package, *sorted(package.rglob('*'))]
    for entry in paths:
        if entry.is_symlink():
            raise AppError('Lien symbolique interdit dans l’application Django secrets.')
    if not shutil.which('setfacl'):
        # Without ACL, check actual permissions with service account instead.
        call(['runuser', '-u', USER, '--', str(VENV / 'bin/python'), '-c',
              'import apps.projects.secrets'], cwd=BACKEND, env=django_env())
        return
    for entry in paths:
        if entry.is_dir():
            call(['setfacl', '-m', f'u:{USER}:r-x', str(entry)])
        elif entry.is_file() and entry.suffix == '.py':
            call(['setfacl', '-m', f'u:{USER}:r--', str(entry)])
    # Fail early with a clear error if a parent directory is inaccessible.
    call(['runuser', '-u', USER, '--', str(VENV / 'bin/python'), '-c',
          'import apps.projects.secrets'], cwd=BACKEND, env=django_env())


def run_checks(*, allow_repair=False):
    if not (BACKEND / 'manage.py').is_file():
        raise AppError('Code Django introuvable.')
    if not SECURITY_RULES.is_file():
        raise AppError(f'Règles Semgrep locales absentes : {SECURITY_RULES}')
    for scanner in ('semgrep', 'trivy'):
        if not shutil.which(scanner):
            raise AppError(f'Scanner DevSecOps obligatoire absent : {scanner}. Installer via AUTOKOM Core.')
    # Authenticate before scanning; offer interactive opt-in repair on real deploy.
    _check_mysql_with_optional_repair(allow_repair=allow_repair)
    # Never mix Semgrep --config auto with --metrics off: use versioned rules.
    with phase('SAST · Semgrep (règles locales, télémétrie désactivée)'):
        call(['semgrep', 'scan', '--config', str(SECURITY_RULES), '--error',
              '--metrics', 'off', '--exclude', '**/.env',
              '--exclude', '**/.venv/**', '--exclude', '**/media/**',
              str(BACKEND)], cwd=ROOT, timeout=600)
    with phase('SCA / secrets · Trivy'):
        call(['trivy', 'fs', '--scanners', 'vuln,secret', '--severity', 'HIGH,CRITICAL',
              '--exit-code', '1', '--no-progress', '--skip-files', str(BACKEND/'.env'),
              '--skip-dirs', str(BACKEND/'media'), str(BACKEND)], cwd=ROOT, timeout=600)
    if not VENV.is_dir() or not (VENV / 'bin/python').is_file():
        raise AppError('Environnement Python absent : lancer autokom app setup.')
    with phase('Django · vérification production'):
        run_as_service([str(VENV/'bin/python'), 'manage.py', 'check', '--deploy'])
    # Migrations MUST be committed in source control, never generated on prod.
    with phase('Django · cohérence des migrations'):
        run_as_service([str(VENV/'bin/python'), 'manage.py', 'makemigrations', '--check', '--dry-run'])


def verify_mysql_service_access():
    """Check actual Django credentials as the systemd user, not the Linux root user.

    No credentials or SQL passwords are printed or passed through argv. The
    Django process reads the exact environment used by the service.
    """
    config = load_dotenv(ENV_FILE)
    required = ('DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')
    absent = [key for key in required if not str(config.get(key, '')).strip()]
    if absent:
        raise AppError('MySQL : variables absentes de la configuration du service : ' + ', '.join(absent))
    script = (
        'import django; django.setup(); '
        'from django.db import connection; '
        'connection.ensure_connection(); '
        'cursor = connection.cursor(); '
        'cursor.execute("SELECT 1"); '
        'assert cursor.fetchone()[0] == 1; '
        'print("Connexion MySQL du backend confirmée (SELECT 1).", flush=True)'
    )
    try:
        run_as_service([str(VENV / 'bin/python'), 'manage.py', 'shell', '-c', script], timeout=45)
    except AppError as exc:
        raise AppError(
            'MySQL inaccessible avec les identifiants du service Django. '
            'Vérifier DB_USER, DB_PASSWORD et DB_HOST dans '
            '/etc/autokom/secrets/backend.env, puis les droits utilisateur@hôte MySQL. '
            'Ne pas créer de migrations ou modifier le mot de passe sans diagnostic. '
            f'Détails dans le journal : {_LIVE.log_path if _LIVE else "voir la sortie précédente"}'
        ) from exc


def _check_mysql_with_optional_repair(*, allow_repair):
    """Verify the real service account; never silently change DB credentials."""
    try:
        with phase('MySQL · authentification du compte applicatif'):
            verify_mysql_service_access()
    except AppError:
        if not allow_repair:
            print('  › Pour une réparation guidée : sudo autokom app doctor')
            raise
        # No DB account may be created if the updated EnvironmentFile cannot
        # be written with our explicit production whitelist.
        unknown = set(load_dotenv(ENV_FILE)) - ALLOWED_ENV_KEYS
        if unknown:
            raise AppError('Variables du .env service non reconnues : ' + ', '.join(sorted(unknown)))
        from autokom_mysql_repair import offer_mysql_repair
        if not offer_mysql_repair(
            runtime_file=ENV_FILE,
            source_file=SOURCE_ENV_CANDIDATES[0],
            python=VENV / 'bin/python',
            load_config=load_dotenv,
            save_config=secure_env_write,
            verify=verify_mysql_service_access,
        ):
            raise AppError('Connexion MySQL non réparée ; aucune migration ni déploiement effectué.')
        with phase('MySQL · vérification après réparation'):
            verify_mysql_service_access()


def doctor(args):
    """Read-only by default noninteractive; TTY may opt into repair explicitly."""
    root_only()
    state = load_state()
    print('  ◈ AUTOKOM · DIAGNOSTIC BACKEND')
    print('  Runtime :', state.get('runtime'))
    print('  Domaine :', state.get('domain'))
    print('  Secrets : /etc/autokom/secrets/backend.env (valeurs masquées)')
    try:
        with operation('doctor'):
            _check_mysql_with_optional_repair(allow_repair=not args.no_repair)
        print('  ✓ MySQL : connexion du service autokom valide')
    except AppError as exc:
        print('  ✗ ' + str(exc))
        return 1
    return 0


def verify_health(port=None):
    if port is None:
        try:
            port = int(load_state().get('port', PORT))
        except (AppError, ValueError, TypeError):
            port = PORT
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health/',
                                     headers={'Host': 'localhost', 'X-Forwarded-Proto': 'https', 'User-Agent': 'AUTOKOM-Health/1'})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.load(response)
            return response.status == 200 and payload.get('data', {}).get('status') == 'ok'
    except (urllib.error.URLError, ValueError, OSError):
        return False


def _is_interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt, default=''):
    answer = input(f'  {prompt} [{default}] : ').strip()
    return answer or default


def _choose(title, options, default):
    """Sélection standard AUTOKOM via /dev/tty (↑/↓, Entrée, q/Ctrl-C).

    N'affiche plus de menu numéroté statique. L'index reste local à ce
    choix, les clés métier sont conservées inchangées.
    """
    from menu import pick
    values = [key for key, _ in options]
    if not values:
        raise AppError('Aucune option disponible pour ' + title)
    default_index = values.index(default) + 1 if default in values else 1
    try:
        selected = pick('AUTOKOM · ' + title,
                        [label for _, label in options], default=default_index,
                        cancel_label='Annuler sans modification')
    except KeyboardInterrupt as exc:
        raise AppError('Configuration annulée par l’administrateur.') from exc
    except RuntimeError as exc:
        raise AppError('Terminal interactif indisponible : fournir les options CLI pour Ansible.') from exc
    if selected == 0:
        raise AppError('Configuration annulée par l’administrateur.')
    return values[selected - 1]


def _candidate_env(explicit):
    if explicit:
        src = Path(explicit)
        if not src.is_absolute():
            raise AppError('--env-file doit être un chemin absolu sous /etc/autokom/.')
        src = src.resolve(strict=True)
        if src != Path('/etc/autokom') and Path('/etc/autokom') not in src.parents:
            raise AppError('Le fichier .env doit se trouver dans /etc/autokom/, hors dépôt.')
        if not src.is_file():
            raise AppError('Fichier .env introuvable.')
        return src
    for src in SOURCE_ENV_CANDIDATES:
        if src.is_file():
            return src
    raise AppError('Aucun fichier serveur : créer /etc/autokom/secrets/backend.env (sans l’envoyer sur GitHub).')


def _derive_domain(config):
    for host in config.get('ALLOWED_HOSTS', '').split(','):
        host = host.strip().lower()
        if host not in ('localhost', '127.0.0.1', '0.0.0.0') and '.' in host and '*' not in host:
            return host
    return ''


def _safe_env_check(src):
    if src.is_symlink():
        raise AppError('Fichier .env symbolique refusé ; choisir un fichier réel dans /etc/autokom/.')
    if Path('/etc/autokom') not in src.parents:
        raise AppError('Le fichier des secrets doit être dans /etc/autokom/.')
    info = src.stat()
    if info.st_uid != 0:
        raise AppError('Le .env de production doit appartenir à root.')
    if info.st_mode & 0o077:
        print('  ATTENTION : le .env est trop accessible ; setup corrigera son mode à 0600.')


def _preflight(args, settings, source):
    if not BACKEND.is_dir() or not (BACKEND / 'manage.py').is_file():
        raise AppError('Code Django introuvable dans platform/backend.')
    if args.runtime not in ('systemd', 'docker'):
        raise AppError('Runtime invalide.')
    if args.nginx_mode == 'existing' and args.tls_mode == 'certbot':
        raise AppError('Nginx existant : choisir --tls-mode external (HTTPS déjà géré).')
    if args.environment not in ('production', 'staging'):
        raise AppError('Environnement invalide.')
    domain = validate_domain(args.domain)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.email or '') and args.tls_mode == 'certbot':
        raise AppError('E-mail Let’s Encrypt valide obligatoire avec Certbot.')
    if not 1024 <= args.port <= 65535:
        raise AppError('Port interne non privilégié requis (1024–65535).')
    if not 1 <= args.workers <= 16:
        raise AppError('Gunicorn : 1–16 workers.')
    if not args.frontend or not re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::[0-9]{2,5})?', args.frontend):
        raise AppError('Frontend HTTPS invalide.')
    if any(not settings.get(key) for key in ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST')) and not args.provision_db:
        raise AppError('Configuration MySQL incomplète dans le .env source.')
    if args.provision_db and args.runtime == 'docker':
        raise AppError('--provision-db est réservé à systemd et doit être explicitement accepté.')
    if args.runtime == 'docker' and settings.get('DB_HOST', '').lower() in ('localhost','127.0.0.1','::1'):
        raise AppError('En Docker, DB_HOST=127.0.0.1 pointe vers le conteneur. Configurer un hôte MySQL joignable.')
    if args.environment == 'production' and args.tls_mode == 'none':
        raise AppError('Le mode production requiert HTTPS (certbot ou external).')
    if settings.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend') != 'django.core.mail.backends.smtp.EmailBackend':
        raise AppError('Le backend doit utiliser EMAIL_BACKEND SMTP pour l’inscription clients.')
    if settings.get('DB_ENGINE', 'mysql').lower() != 'mysql':
        raise AppError('Seule la base MySQL est prise en charge.')
    if not settings.get('EMAIL_HOST') or not settings.get('EMAIL_HOST_USER') or not settings.get('EMAIL_HOST_PASSWORD'):
        raise AppError('SMTP incomplet : EMAIL_HOST, EMAIL_HOST_USER et EMAIL_HOST_PASSWORD requis.')
    if any(not settings.get(k) or settings[k].startswith('CHANGE_ME')
           for k in ('DJANGO_SECRET_KEY', 'CLIENT_JWT_SIGNING_KEY')):
        raise AppError('DJANGO_SECRET_KEY et CLIENT_JWT_SIGNING_KEY doivent être configurés.')
    unknown = sorted(set(settings) - ALLOWED_ENV_KEYS)
    if unknown:
        raise AppError('Variables .env non reconnues : ' + ', '.join(unknown))
    _safe_env_check(source)
    if STATE_FILE.exists():
        state = load_state()
        changed = [key for key, new in (('runtime', args.runtime), ('domain', domain), ('port', args.port))
                   if state.get(key) != new]
        if changed:
            raise AppError('Installation existante avec paramètres différents (' + ', '.join(changed)
                           + '). Migration explicite requise, refus d’écraser.')
    if args.nginx_mode == 'managed' and not shutil.which('nginx') and not ANSIBLE_PLAYBOOK.is_file():
        raise AppError('Nginx absent et playbook de dépendances introuvable.')
    return domain


def _print_setup_plan(args, source, settings):
    print('\n  AUTOKOM Platform · Plan de configuration du backend')
    for label, value in (
        ('Environnement', args.environment), ('Runtime', args.runtime),
        ('Utilisateur', USER if args.runtime == 'systemd' else 'autokom (container)'),
        ('Domaine backend', args.domain), ('Frontend', args.frontend),
        ('Proxy Nginx', args.nginx_mode), ('HTTPS', args.tls_mode),
        ('Port local', f'127.0.0.1:{args.port}'), ('Workers Gunicorn', str(args.workers)),
        ('Configuration secrète source', str(source)),
        ('Configuration secrète service', str(ENV_FILE)),
        ('MySQL', f"{settings.get('DB_HOST', '?')}:{settings.get('DB_PORT','3306')}/{settings.get('DB_NAME', '?')}"),
        ('Base existante', 'conservée' if not args.provision_db else 'création uniquement si absente'),
        ('Django', 'DEBUG=False, MySQL, HTTPS/CORS/CSRF, JWT et SMTP conservés'),
        ('DevSecOps', 'Semgrep, Trivy, Django checks à la commande app deploy'),
    ):
        print(f'    {label:<27} {value}')
    print('  Aucun secret ne sera affiché. Aucun déploiement ne sera déclenché par setup.\n')


def _wizard_business_settings(settings):
    print('\n  Paramètres existants détectés (les mots de passe ne sont jamais affichés) :')
    for label, key in [('MySQL', 'DB_NAME'), ('SMTP', 'EMAIL_HOST'),
                       ('GitHub App clients', 'GITHUB_APP_CLIENT_ID'),
                       ('Clé de chiffrement', 'AUTOKOM_FIELD_ENCRYPTION_KEY')]:
        print(f'    {label:<25} {"configuré" if settings.get(key) else "absent"}')
    choice = _choose('Configuration des services applicatifs',
        [('keep','Conserver les paramètres du backend.env'),
         ('edit','Modifier MySQL, SMTP ou GitHub App pour cette installation')], 'keep')
    if choice == 'keep':
        return settings
    which = _choose('Configurer',
        [('mysql','MySQL'), ('smtp','SMTP'), ('github','GitHub App des clients')], 'mysql')
    if which == 'mysql':
        for key, title, default in (
            ('DB_HOST','Serveur MySQL', '127.0.0.1'), ('DB_PORT','Port MySQL','3306'),
            ('DB_NAME','Base MySQL','autokom_platform'), ('DB_USER','Utilisateur MySQL','autokom')):
            settings[key] = _ask(title, settings.get(key, default))
        secret = getpass.getpass('  Mot de passe MySQL (Entrée = conserver) : ')
        if secret: settings['DB_PASSWORD'] = secret
    elif which == 'smtp':
        for key, title, default in (
            ('EMAIL_HOST','Hôte SMTP',''), ('EMAIL_PORT','Port SMTP','587'),
            ('EMAIL_HOST_USER','Identifiant SMTP',''),
            ('DEFAULT_FROM_EMAIL','Adresse expéditeur','noreply@localhost')):
            settings[key] = _ask(title, settings.get(key, default))
        settings['EMAIL_BACKEND'] = 'django.core.mail.backends.smtp.EmailBackend'
        settings['EMAIL_USE_TLS'] = _choose('SMTP STARTTLS', [('True', 'Activer STARTTLS'), ('False', 'Désactiver STARTTLS')], 'False' if str(settings.get('EMAIL_USE_TLS', 'True')).lower() == 'false' else 'True')
        secret = getpass.getpass('  Mot de passe SMTP (Entrée = conserver) : ')
        if secret: settings['EMAIL_HOST_PASSWORD'] = secret
    else:
        for key, title in (
            ('GITHUB_APP_CLIENT_ID','GitHub App Client ID'),
            ('GITHUB_APP_SLUG','GitHub App slug'),
            ('GITHUB_APP_CALLBACK_URL','GitHub Callback URL')):
            settings[key] = _ask(title, settings.get(key,''))
        for key, title in (
            ('GITHUB_APP_CLIENT_SECRET','GitHub App Client secret'),
            ('GITHUB_WEBHOOK_SECRET','GitHub Webhook secret'),
            ('AUTOKOM_FIELD_ENCRYPTION_KEY','Clé Fernet des données clients')):
            secret = getpass.getpass(f'  {title} (Entrée = conserver) : ')
            if secret: settings[key] = secret
    return settings


def _wizard(args):
    print('\n  AUTOKOM · Assistant de configuration du backend\n')
    args.runtime = _choose('Mode d’exécution', [('systemd','Système (systemd + Gunicorn)'),
                                                  ('docker','Docker Compose')], args.runtime)
    args.environment = _choose('Environnement', [('production','Production'),
                                                         ('staging','Préproduction / staging')], args.environment)
    source = _candidate_env(args.env_file)
    print('  Fichier serveur détecté :', source)
    args.env_file = _ask('Fichier .env sur le serveur', str(source))
    settings = load_dotenv(Path(args.env_file))
    settings = _wizard_business_settings(settings)
    args._wizard_values = settings
    args.domain = _ask('Domaine du backend', args.domain or _derive_domain(settings))
    args.frontend = _ask('URL HTTPS du frontend', args.frontend or settings.get('FRONTEND_URL', ''))
    args.port = int(_ask('Port interne (boucle locale)', str(args.port)))
    args.workers = int(_ask('Nombre de workers Gunicorn', str(args.workers)))
    args.nginx_mode = _choose('Configuration du reverse proxy',
        [('managed','Gérer le vhost Nginx AUTOKOM'),('existing','Nginx existant (ne pas modifier)')], args.nginx_mode)
    tls_options = ([('certbot','Certbot : obtenir ou conserver le certificat'),
                    ('external','TLS déjà géré par un proxy externe / existant'),
                    ('none','HTTP seulement (environnement isolé)')]
                   if args.nginx_mode == 'managed' else
                   [('external','HTTPS déjà géré par le Nginx existant'),
                    ('none','HTTP seulement (environnement isolé)')])
    args.tls_mode = _choose('Gestion HTTPS', tls_options,
                            args.tls_mode if any(k == args.tls_mode for k, _ in tls_options) else 'external')
    if args.tls_mode == 'certbot':
        email = settings.get('DEFAULT_FROM_EMAIL', '').strip()
        args.email = _ask('E-mail Let’s Encrypt', args.email or email)
    args.provision_db = False if all(settings.get(k) for k in ('DB_NAME','DB_USER','DB_PASSWORD')) else (
        _choose('Créer une base MySQL si absente ?',
                [('non', 'Non, conserver la configuration'),
                 ('oui', 'Oui, provisionner une nouvelle base')], 'non') == 'oui')
    return args


def _install_os_requirements(runtime):
    if not ANSIBLE_PLAYBOOK.is_file():
        raise AppError('Playbook Ansible platform-backend.yml manquant.')
    if not shutil.which('ansible-playbook'):
        raise AppError('Ansible absent : lancer la fondation AUTOKOM Core avant app setup.')
    config = ROOT / 'ansible/ansible.cfg'
    env = dict(os.environ)
    if config.is_file():
        env['ANSIBLE_CONFIG'] = str(config)
    inventory = ROOT / 'ansible/inventories/local/hosts.ini'
    if not inventory.is_file():
        raise AppError('Inventaire Ansible local introuvable.')
    call(['ansible-playbook', '-i', str(inventory), str(ANSIBLE_PLAYBOOK),
          '-e', f'autokom_backend_runtime={runtime}'], cwd=ROOT, env=env, timeout=900)


def setup(args):
    # Without flags on a real terminal, setup becomes an interactive wizard.
    if args.wizard:
        if not _is_interactive():
            raise AppError('Assistant interactif : lancer dans un vrai terminal ou renseigner les options.')
        try:
            args = _wizard(args)
        except (ValueError, EOFError, KeyboardInterrupt):
            raise AppError('Saisie annulée ou invalide.') from None
    elif not args.domain or not args.frontend:
        raise AppError('Utiliser autokom app setup (assistant) ou fournir --domain et --frontend.')
    source = _candidate_env(args.env_file)
    values = load_dotenv(source)
    if hasattr(args, '_wizard_values'):
        values.update(args._wizard_values)
    domain = _preflight(args, values, source)
    _print_setup_plan(args, source, values)
    if args.check:
        print('  Vérification uniquement : aucune modification effectuée.')
        return 0
    root_only()
    if not args.yes:
        if not _is_interactive() or input('  Taper INSTALLER pour confirmer : ').strip() != 'INSTALLER':
            raise AppError('Installation annulée (aucune modification).')
    with operation('setup'):
        with phase('Ansible · dépendances système'):
            _install_os_requirements(args.runtime)
        with phase('Compte système · permissions minimales'):
            create_user()
        if args.provision_db:
            with phase('MySQL · initialisation contrôlée'):
                provision_database(values)
        values.update({
            'DEBUG': 'False', 'DB_ENGINE': 'mysql',
            'ALLOWED_HOSTS': f'{domain},127.0.0.1,localhost',
            'CSRF_TRUSTED_ORIGINS': f'https://{domain},' + args.frontend,
            'CORS_ALLOWED_ORIGINS': args.frontend,
            'FRONTEND_URL': args.frontend,
            'TRUST_PROXY_SSL_HEADER': 'true',
            'SECURE_SSL_REDIRECT': 'false' if args.tls_mode == 'none' else 'true',
            'SECURE_HSTS_SECONDS': '0' if args.tls_mode == 'none' else '31536000',
            'AUTOKOM_STATIC_ROOT': '/var/lib/autokom/staticfiles',
            'AUTOKOM_MEDIA_ROOT': '/var/lib/autokom/media',
            'AUTOKOM_GUNICORN_WORKERS': str(args.workers),
            'AUTOKOM_ENV_NAME': args.environment,
        })
        # Secrets in /etc/autokom only; preserve original source and all app-specific keys.
        with phase('Configuration Django · secrets et sécurité'):
            secure_env_write(values)
        if source != ENV_FILE:
            os.chmod(source, 0o600)
        if args.runtime == 'systemd':
            with phase('Python · installation des dépendances'):
                install_python()
            with phase('systemd · création du service'):
                write_service(args.port, args.workers)
        elif not shutil.which('docker'):
            raise AppError('Docker indisponible après provisioning des prérequis.')
        if args.nginx_mode == 'managed':
            with phase('Nginx / TLS · configuration sécurisée'):
                write_nginx(domain, args.email, issue_tls=args.tls_mode == 'certbot',
                            runtime=args.runtime, port=args.port)
        state = {'runtime': args.runtime, 'environment': args.environment, 'domain': domain,
                 'frontend': args.frontend, 'port': args.port, 'workers': args.workers,
                 'env_source': str(source), 'nginx_mode': args.nginx_mode, 'tls_mode': args.tls_mode}
        temp = STATE_FILE.with_suffix('.new')
        temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        temp.chmod(0o600)
        os.replace(temp, STATE_FILE)
    print('  Setup terminé. La mise en service se fait avec : sudo autokom app deploy --check, puis deploy')
    if args.tls_mode == 'none':
        print('  ATTENTION : HTTP uniquement. Ne pas exposer les clients en production.')

def _compose_env(state):
    env = dict(os.environ)
    env['AUTOKOM_BACKEND_PORT'] = str(state.get('port', PORT))
    env['AUTOKOM_BACKEND_WORKERS'] = str(state.get('workers', 2))
    env['AUTOKOM_BACKEND_ENV_FILE'] = str(ENV_FILE)
    return env


def deploy(args):
    root_only()
    state = load_state()
    with operation('deploy') as progress:
        with phase('Préparation · vérification du code et de la configuration'):
            if not (BACKEND / 'manage.py').is_file():
                raise AppError('Code backend manquant.')
            if not ENV_FILE.is_file():
                raise AppError('Fichier de configuration sécurisé manquant.')
        if state['runtime'] == 'systemd':
            with phase('Django · présence du code applicatif'):
                package = BACKEND / 'apps' / 'projects' / 'secrets'
                if not (package / '__init__.py').is_file() or not (package / 'apps.py').is_file():
                    raise AppError('Application Django apps.projects.secrets absente du dépôt.')
                # A dry-run must not modify filesystem ACLs.
                if not args.check:
                    ensure_django_secrets_package_access()
        run_checks(allow_repair=not args.check)
        if args.check:
            print('  ✓ Contrôles DevSecOps terminés · aucun changement déployé.')
            return 0
        if state['runtime'] == 'systemd':
            with phase('MySQL · migrations Django versionnées'):
                run_as_service([str(VENV/'bin/python'), 'manage.py', 'migrate', '--noinput'], timeout=300)
            with phase('Staticfiles · collecte'):
                run_as_service([str(VENV/'bin/python'), 'manage.py', 'collectstatic', '--noinput'], timeout=300)
                if shutil.which('setfacl'):
                    call(['setfacl', '-R', '-m', 'u:www-data:r-X', '/var/lib/autokom/staticfiles'])
            with phase('systemd · démarrage / redémarrage de Gunicorn'):
                call(['systemctl', 'restart', SERVICE])
                call(['systemctl', 'is-active', '--quiet', SERVICE])
        else:
            with phase('Docker · construction et démarrage'):
                call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
                      'up', '-d', '--build'], cwd=BACKEND, timeout=900, env=_compose_env(state))
            with phase('Docker · migrations Django'):
                call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
                      'exec', '-T', 'web', 'python', 'manage.py', 'migrate', '--noinput'],
                     cwd=BACKEND, timeout=300, env=_compose_env(state))
            with phase('Docker · collecte des staticfiles'):
                call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
                      'exec', '-T', 'web', 'python', 'manage.py', 'collectstatic', '--noinput'],
                     cwd=BACKEND, timeout=300, env=_compose_env(state))
        with phase('Disponibilité · réponse HTTP /api/health/'):
            for _ in range(25):
                if verify_health(port=state.get('port', PORT)):
                    print('  ✓ Backend opérationnel sur http://127.0.0.1:' + str(state.get('port', PORT)))
                    print('  ✓ URL publique prévue : https://' + state.get('domain', '?'))
                    return 0
                if progress._tty:
                    progress.paint('Vérification du health check', _ * 2)
                time.sleep(2)
        raise AppError('Gunicorn a été lancé mais le health check est indisponible. '
                       'Vérifier autokom app logs et le journal de déploiement.')


def status(args):
    try:
        state = load_state()
    except AppError as exc:
        print('  ✗ '+str(exc))
        return 1
    print('  ◈ AUTOKOM Platform · État')
    print('  Environnement :', state.get('environment', 'production'))
    print('  Runtime :', state['runtime'])
    print('  Domaine :', state['domain'])
    if state.get('preferred_domain'):
        print('  Domaine HTTPS préféré :', state['preferred_domain'])
    if state.get('domain_aliases'):
        print('  Domaines supplémentaires :', ', '.join(state['domain_aliases']))
    if state['runtime'] == 'systemd':
        proc = subprocess.run(['systemctl', 'is-active', SERVICE], capture_output=True,
                              text=True, check=False, timeout=15)
        service_state = proc.stdout.strip() or 'unknown'
        print('  Service systemd :', service_state)
        if service_state == 'inactive':
            print('  › Setup effectué, backend non démarré. Lancer : sudo autokom app deploy')
        elif service_state == 'failed':
            print('  ✗ Service en échec. Diagnostic : sudo journalctl -u autokom-backend -n 80 --no-pager')
    else:
        proc = subprocess.run(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
                               'ps'], cwd=BACKEND, timeout=30, env=_compose_env(state), check=False)
        if proc.returncode:
            print('  ✗ Docker compose indisponible ou conteneur arrêté.')
    healthy = verify_health(port=state.get('port', PORT))
    print('  Health check local :', 'OK' if healthy else 'INDISPONIBLE')
    if not healthy:
        print('  › Un 502 Nginx est normal tant que Gunicorn ne répond pas sur le port local.')
    return 0 if healthy else 1


def logs(args):
    state = load_state()
    if args.source in ('setup', 'deploy'):
        candidates = sorted(OPERATION_LOGS.glob(args.source + '-*.log')) if OPERATION_LOGS.is_dir() else []
        if not candidates:
            raise AppError('Aucun journal de '+args.source+' disponible.')
        selected = candidates[-1]
        if os.geteuid() != 0:
            raise AppError('Les journaux détaillés sont réservés à root : utiliser sudo.')
        print('  Journal :', selected)
        lines = selected.read_text(encoding='utf-8', errors='replace').splitlines()[-args.lines:]
        for line in lines:
            print(line)
        return 0
    if state['runtime'] == 'systemd':
        call(['journalctl', '-u', SERVICE, '-n', str(args.lines), '--no-pager'], timeout=30)
    else:
        call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
              'logs', '--tail', str(args.lines), 'web'], cwd=BACKEND, timeout=30, env=_compose_env(state))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='autokom app', description='Gestion du service backend AUTOKOM Platform')
    sub = parser.add_subparsers(dest='action', required=True)
    setup_p = sub.add_parser('setup', help='Assistant de configuration backend (sans option : interactif)')
    setup_p.add_argument('--domain', help='Domaine public du backend Django')
    setup_p.add_argument('--email', help='Adresse Let’s Encrypt (requise pour --tls-mode certbot)')
    setup_p.add_argument('--frontend', help='URL HTTPS du frontend (CORS)')
    setup_p.add_argument('--runtime', choices=['systemd','docker'], default='systemd')
    setup_p.add_argument('--environment', choices=['production','staging'], default='production')
    setup_p.add_argument('--env-file', help='Chemin absolu du backend.env dans /etc/autokom/')
    setup_p.add_argument('--port', type=int, default=PORT, help='Port local, défaut 8172')
    setup_p.add_argument('--workers', type=int, default=2, help='Workers Gunicorn (1–16)')
    setup_p.add_argument('--nginx-mode', choices=['managed','existing'], default='managed')
    setup_p.add_argument('--tls-mode', choices=['certbot','external','none'], default='certbot')
    setup_p.add_argument('--provision-db', action='store_true', help='Créer MySQL seulement si aucun DB existant')
    setup_p.add_argument('--check', action='store_true', help='Prévisualisation et diagnostic sans modification')
    setup_p.add_argument('--yes', action='store_true', help='Confirmer en mode non interactif (Ansible)')
    setup_p.add_argument('--wizard', action='store_true', help='Forcer l’assistant interactif')
    deploy_p = sub.add_parser('deploy', help='Contrôles DevSecOps et déploiement Django')
    deploy_p.add_argument('--check', action='store_true', help='Scanner et vérifier sans déployer')
    doctor_p = sub.add_parser('doctor', help='Diagnostiquer MySQL, proposer la réparation sur confirmation')
    doctor_p.add_argument('--no-repair', action='store_true', help='Diagnostic strictement en lecture seule')
    sub.add_parser('status', help='État du service et health check')
    admin_p = sub.add_parser('admin', help='Gérer les administrateurs Django (création, liste, accès)')
    admin_p.add_argument('admin_args', nargs=argparse.REMAINDER, help='create | list | reset-password | disable | enable')
    log_p = sub.add_parser('logs', help='Dernières lignes de journaux')
    log_p.add_argument('--lines', type=int, default=80)
    log_p.add_argument('--source', choices=['service', 'setup', 'deploy'], default='service',
                       help='Service Gunicorn ou journal détaillé de setup/deploy')
    for env_kind in ('backend', 'frontend'):
        env_p = sub.add_parser(env_kind, help='Configuration '+env_kind+' : env show/setup/edit/delete', add_help=False)
        env_p.add_argument('backend_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.action in ('backend','frontend'):
        from autokom_env_manager import main as env_main, EnvError
        if args.action == 'backend' and args.backend_args and args.backend_args[0] == 'domain':
            from autokom_backend_domain import main as domain_main, DomainError
            from autokom_dns_core import DNSError
            from autokom_domain_dns import DNSMigrationError
            from autokom_domain_migration import MigrationError
            try: return domain_main(args.backend_args[1:])
            except (DomainError, DNSError, DNSMigrationError, MigrationError) as exc:
                raise AppError(str(exc)) from exc
        if not args.backend_args or args.backend_args[0] != 'env':
            raise AppError('Utiliser : autokom app '+args.action+' env [show|setup|edit|delete] ou backend domain [--check|--apply|--status]')
        try: return env_main(args.action,args.backend_args[1:])
        except EnvError as exc: raise AppError(str(exc)) from exc
    if args.action == 'setup':
        if not args.domain and not args.yes and _is_interactive():
            args.wizard = True
        return setup(args)
    if args.action == 'deploy': return deploy(args)
    if args.action == 'doctor': return doctor(args)
    if args.action == 'status': return status(args)
    if args.action == 'admin':
        from autokom_admin_manager import main as admin_main, AdminError
        try:
            return admin_main(args.admin_args)
        except AdminError as exc:
            raise AppError(str(exc)) from exc
    if args.action == 'logs': return logs(args)
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except AppError as exc:
        print('AUTOKOM : '+str(exc), file=sys.stderr)
        sys.exit(1)
