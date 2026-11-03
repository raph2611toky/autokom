#!/usr/bin/env python3
"""AUTOKOM Platform backend lifecycle (systemd by default, Docker optional).

Root privileges are used only in explicit setup/deploy operations, never by the
Django application process. Does not install arbitrary client applications.
"""
from __future__ import annotations

import argparse
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
ENV_FILE = CONFIG_DIR / 'platform-backend.env'
STATE_FILE = CONFIG_DIR / 'app-runtime.json'
USER = 'autokom'
SERVICE = 'autokom-backend'
VENV = Path('/opt/autokom/venv')
PORT = 8172


class AppError(RuntimeError):
    pass


def call(args, *, cwd=None, input_text=None, timeout=120, env=None):
    try:
        return subprocess.run(args, cwd=cwd, input=input_text, text=True, timeout=timeout,
                              check=True, env=env)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        raise AppError(f'Échec de {args[0]} ({getattr(exc, "returncode", "interrompu")}).') from exc


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


def secure_env_write(values):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Never copy arbitrary .env keys to systemd. Explicit whitelist prevents
    # accidental EnvironmentFile injection and stale/wrong runtime settings.
    allowed = {
        'DEBUG', 'DJANGO_SECRET_KEY', 'CLIENT_JWT_SIGNING_KEY',
        'CLIENT_JWT_ISSUER', 'CLIENT_JWT_AUDIENCE',
        'CLIENT_ACCESS_MINUTES', 'CLIENT_REFRESH_DAYS',
        'DB_TEST_NAME', 'EMAIL_USE_SSL',
        'AES_KEY', 'AES_IV',
        'DB_ENGINE', 'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
        'ALLOWED_HOSTS', 'FRONTEND_URL', 'CSRF_TRUSTED_ORIGINS', 'CORS_ALLOWED_ORIGINS',
        'EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS', 'EMAIL_HOST_USER',
        'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL', 'GITHUB_APP_CLIENT_ID',
        'GITHUB_APP_CLIENT_SECRET', 'GITHUB_APP_SLUG', 'GITHUB_APP_CALLBACK_URL',
        'GITHUB_WEBHOOK_SECRET', 'GITHUB_APP_ID', 'GITHUB_APP_PRIVATE_KEY_PATH',
        'AUTOKOM_FIELD_ENCRYPTION_KEY', 'AUTOKOM_BASE_DOMAIN', 'GITHUB_API_VERSION',
        'AUTOKOM_STATIC_ROOT', 'AUTOKOM_MEDIA_ROOT', 'TRUST_PROXY_SSL_HEADER',
        'SECURE_SSL_REDIRECT', 'SECURE_HSTS_SECONDS', 'AUTOKOM_SERVE_STATIC',
    }
    output = []
    for key in sorted(set(values) & allowed):
        val = str(values[key])
        if any(ch in val for ch in ('\n', '\r', '\x00')):
            raise AppError('Valeur d’environnement invalide : ' + key)
        # Values use systemd EnvironmentFile double-quoted escaping.
        escaped = val.replace('\\', '\\\\').replace('"', '\\"')
        output.append(f'{key}="{escaped}"')
    tmp = ENV_FILE.with_suffix('.new')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(output) + '\n')
        shutil.chown(tmp, 'root', USER)
        os.chmod(tmp, 0o640)
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
            if source.is_symlink() or any(piece in {'.git', '.venv', '__pycache__', 'secrets', '.env'}
                                           for piece in relative.parts):
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


def write_service():
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
ExecStart={VENV}/bin/gunicorn config.wsgi:application --bind 127.0.0.1:{PORT} --workers 2 --timeout 90 --access-logfile - --error-logfile -
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


def write_nginx(domain, email, *, issue_tls, runtime):
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
                       f'location /static/ {{ proxy_pass http://127.0.0.1:{PORT}; }}')
    conf = f'''# MANAGED BY AUTOKOM PLATFORM
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    client_max_body_size 20m;
    {static_location}
    location / {{
        proxy_pass http://127.0.0.1:{PORT};
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
        raise AppError('Configuration /etc/autokom/platform-backend.env manquante.')
    data = load_dotenv(ENV_FILE)
    env = dict(os.environ)
    env.update(data)
    return env


def run_as_service(args, *, timeout=300):
    env = django_env()
    # Explicitly drop privileges to the system user for Django operations.
    command = ['runuser', '-u', USER, '--', *args]
    return call(command, cwd=BACKEND, env=env, timeout=timeout)


def run_checks():
    if not (BACKEND / 'manage.py').is_file():
        raise AppError('Code Django introuvable.')
    for scanner in ('semgrep', 'trivy'):
        if not shutil.which(scanner):
            raise AppError(f'Scanner obligatoire absent : {scanner}. Installer via AUTOKOM Core.')
    # Scans target the platform backend only, not other customer repositories.
    call(['semgrep', 'scan', '--config', 'auto', '--error', '--metrics', 'off',
          '--exclude', '**/.env', str(BACKEND)], cwd=ROOT, timeout=600)
    call(['trivy', 'fs', '--scanners', 'vuln,secret', '--severity', 'HIGH,CRITICAL',
          '--exit-code', '1', '--no-progress', '--skip-files', str(BACKEND/'.env'),
          '--skip-dirs', str(BACKEND/'media'), str(BACKEND)], cwd=ROOT, timeout=600)
    if VENV.exists():
        run_as_service([str(VENV/'bin/python'), 'manage.py', 'check', '--deploy'])
        # Migrations MUST be committed in source control, never generated on prod.
        run_as_service([str(VENV/'bin/python'), 'manage.py', 'makemigrations', '--check', '--dry-run'])


def verify_health():
    request = urllib.request.Request(f'http://127.0.0.1:{PORT}/api/health/',
                                     headers={'Host': 'localhost', 'X-Forwarded-Proto': 'https', 'User-Agent': 'AUTOKOM-Health/1'})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.load(response)
            return response.status == 200 and payload.get('data', {}).get('status') == 'ok'
    except (urllib.error.URLError, ValueError, OSError):
        return False


def setup(args):
    root_only()
    if not BACKEND.is_dir():
        raise AppError('platform/backend introuvable dans le dépôt AUTOKOM.')
    domain = validate_domain(args.domain)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.email):
        raise AppError('Adresse e-mail Certbot invalide.')
    if STATE_FILE.exists() and load_state().get('runtime') != args.runtime:
        raise AppError('Un autre runtime est déjà configuré ; migration manuelle nécessaire.')
    create_user()
    settings = load_dotenv(ENV_FILE) if ENV_FILE.exists() else load_dotenv(BACKEND / '.env')
    if args.provision_db:
        if args.runtime == 'docker':
            raise AppError('En mode Docker, configurer un MySQL accessible dans un réseau privé. --provision-db est réservé à systemd.')
        provision_database(settings)
    elif not all(settings.get(k) for k in ('DB_NAME', 'DB_USER', 'DB_PASSWORD')):
        raise AppError('Configurer MySQL dans backend/.env ou ajouter --provision-db.')
    if args.runtime == 'docker' and settings.get('DB_HOST', '127.0.0.1') in ('127.0.0.1', 'localhost', '::1'):
        raise AppError('MySQL Docker doit être joignable depuis le conteneur : DB_HOST ne peut pas être localhost.')
    settings.update({
        'DEBUG': 'false', 'DB_ENGINE': 'mysql',
        'ALLOWED_HOSTS': f'{domain},127.0.0.1,localhost',
        'CSRF_TRUSTED_ORIGINS': f'https://{domain}',
        'TRUST_PROXY_SSL_HEADER': 'true',
        'SECURE_SSL_REDIRECT': 'true' if not args.no_tls else 'false',
        'SECURE_HSTS_SECONDS': '31536000' if not args.no_tls else '0',
        'AUTOKOM_STATIC_ROOT': '/var/lib/autokom/staticfiles',
        'AUTOKOM_MEDIA_ROOT': '/var/lib/autokom/media',
    })
    if args.frontend:
        settings['FRONTEND_URL'] = args.frontend
        settings['CORS_ALLOWED_ORIGINS'] = args.frontend
    if not settings.get('DJANGO_SECRET_KEY') or settings['DJANGO_SECRET_KEY'].startswith('CHANGE_ME'):
        settings['DJANGO_SECRET_KEY'] = secrets.token_urlsafe(56)
    if not settings.get('CLIENT_JWT_SIGNING_KEY') or settings['CLIENT_JWT_SIGNING_KEY'].startswith('CHANGE_ME'):
        settings['CLIENT_JWT_SIGNING_KEY'] = secrets.token_urlsafe(56)
    if not settings.get('EMAIL_BACKEND') or 'console' in settings['EMAIL_BACKEND']:
        raise AppError('Configurer un backend SMTP réel dans backend/.env avant setup (inscription clients).')
    if args.runtime == 'systemd':
        install_python()
    else:
        if not shutil.which('docker'):
            raise AppError('Docker absent. Installer Docker avant --runtime docker.')
        if not (BACKEND / 'docker-compose.autokom.yml').is_file():
            raise AppError('Fichier Docker Compose AUTOKOM manquant.')
    secure_env_write(settings)
    if args.update_github_client_id:
        if not re.fullmatch(r'[A-Za-z0-9_]{8,128}', args.update_github_client_id):
            raise AppError('GitHub Client ID de mise à jour invalide.')
        update_file = CONFIG_DIR / 'update.env'
        update_file.write_text('AUTOKOM_UPDATE_GITHUB_CLIENT_ID=' + args.update_github_client_id + '\n', encoding='utf-8')
        os.chmod(update_file, 0o600)
    if args.runtime == 'systemd':
        write_service()
    write_nginx(domain, args.email, issue_tls=not args.no_tls, runtime=args.runtime)
    STATE_FILE.write_text(json.dumps({'runtime': args.runtime, 'domain': domain,
                                      'port': PORT}, indent=2)+'\n', encoding='utf-8')
    STATE_FILE.chmod(0o640)
    print('  Configuration terminée. Ensuite : sudo autokom app deploy')
    if args.no_tls:
        print('  ATTENTION : HTTPS non émis. Ne pas exposer les API clients avant Certbot.')


def deploy(args):
    root_only()
    state = load_state()
    if args.check:
        run_checks()
        print('  Vérifications DevSecOps réussies ; aucune migration ni publication.')
        return 0
    run_checks()
    if state['runtime'] == 'systemd':
        run_as_service([str(VENV/'bin/python'), 'manage.py', 'migrate', '--noinput'], timeout=300)
        run_as_service([str(VENV/'bin/python'), 'manage.py', 'collectstatic', '--noinput'], timeout=300)
        if shutil.which('setfacl'):
            call(['setfacl', '-R', '-m', 'u:www-data:r-X', '/var/lib/autokom/staticfiles'])
        call(['systemctl', 'restart', SERVICE])
        call(['systemctl', 'is-active', '--quiet', SERVICE])
    else:
        call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
              'up', '-d', '--build'], cwd=BACKEND, timeout=900)
        call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
              'exec', '-T', 'web', 'python', 'manage.py', 'migrate', '--noinput'], cwd=BACKEND, timeout=300)
        call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
              'exec', '-T', 'web', 'python', 'manage.py', 'collectstatic', '--noinput'], cwd=BACKEND, timeout=300)
    # Django startup may be delayed; retry without falsely reporting success.
    import time
    for _ in range(25):
        if verify_health():
            print('  Déploiement AUTOKOM Platform réussi · /api/health/ = 200')
            return 0
        time.sleep(2)
    raise AppError('Le backend ne répond pas au health check. Voir autokom app logs.')


def status(args):
    try:
        state = load_state()
    except AppError as exc:
        print(str(exc))
        return 1
    print('  Runtime :', state['runtime'])
    print('  Domaine :', state['domain'])
    if state['runtime'] == 'systemd':
        call(['systemctl', 'status', '--no-pager', '--lines=12', SERVICE], timeout=15)
    else:
        call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
              'ps'], cwd=BACKEND, timeout=30)
    print('  Health check :', 'OK' if verify_health() else 'INDISPONIBLE')


def logs(args):
    state = load_state()
    if state['runtime'] == 'systemd':
        call(['journalctl', '-u', SERVICE, '-n', str(args.lines), '--no-pager'], timeout=30)
    else:
        call(['docker', 'compose', '-f', str(BACKEND/'docker-compose.autokom.yml'),
              'logs', '--tail', str(args.lines), 'web'], cwd=BACKEND, timeout=30)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='autokom app', description='Gestion du service backend AUTOKOM Platform')
    sub = parser.add_subparsers(dest='action', required=True)
    setup_p = sub.add_parser('setup', help='Installer le backend, le service et le reverse proxy')
    setup_p.add_argument('--domain', required=True)
    setup_p.add_argument('--email', required=True)
    setup_p.add_argument('--frontend', help='URL HTTPS du frontend (CORS)')
    setup_p.add_argument('--runtime', choices=['systemd','docker'], default='systemd')
    setup_p.add_argument('--update-github-client-id', help='Client ID GitHub App autorisée à lire le dépôt privé AUTOKOM')
    setup_p.add_argument('--provision-db', action='store_true', help='Créer une base et un utilisateur MySQL dédiés si absents')
    setup_p.add_argument('--no-tls', action='store_true', help='Configurer HTTP seulement (tests en interne)')
    deploy_p = sub.add_parser('deploy', help='Contrôles DevSecOps et déploiement Django')
    deploy_p.add_argument('--check', action='store_true', help='Scanner et vérifier sans déployer')
    sub.add_parser('status', help='État du service et health check')
    log_p = sub.add_parser('logs', help='Dernières lignes de journaux')
    log_p.add_argument('--lines', type=int, default=80)
    args = parser.parse_args(argv)
    if args.action == 'setup': return setup(args)
    if args.action == 'deploy': return deploy(args)
    if args.action == 'status': return status(args)
    if args.action == 'logs': return logs(args)
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except AppError as exc:
        print('AUTOKOM : '+str(exc), file=sys.stderr)
        sys.exit(1)
