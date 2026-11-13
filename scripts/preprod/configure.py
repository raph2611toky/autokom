"""Run as root from the fixed staging checkout; never emit credential values."""
import base64
import fcntl
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile

LIB = Path(__file__).resolve().parents[1] / 'lib'
sys.path.insert(0, str(LIB))
from autokom_env_manager import read_env

ETC = Path('/etc/autokom-preprod')
STATE = Path('/var/lib/autokom-preprod')
ROOT = Path('/opt/autokom-preprod')
HOST = 'pre.auto.ikom.mg'


def private(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Fichier de configuration attendu en root:0600.')
    return read_env(path)


def write_once(path, values):
    try:
        with path.open('x') as stream:
            for k, v in values.items():
                if any(c in v for c in '\r\n\x00') or "'" in v:
                    raise ValueError('Valeur de configuration non supportée.')
                stream.write(f"{k}='{v}'\n")
    except FileExistsError:
        private(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def init():
    old_runtime = private(Path('/etc/autokom/secrets/runtime.env'))
    old_dns = private(Path('/etc/autokom/secrets/cloudflare-dns.env'))
    if not ipaddress.IPv4Address(old_runtime['AUTOKOM_INGRESS_IPV4']).is_global:
        raise ValueError('IP publique de la VM principale absente.')
    write_once(ETC / 'backend.env', {
        'DB_NAME': 'autokom_preprod', 'DB_USER': 'autokom_preprod',
        'DB_PASSWORD': secrets.token_hex(32), 'DB_HOST': '127.0.0.1', 'DB_PORT': '3306',
        'DB_TEST_NAME': 'test_autokom_preprod', 'DEBUG': 'false',
        'DJANGO_SECRET_KEY': secrets.token_hex(48), 'CLIENT_JWT_SIGNING_KEY': secrets.token_hex(48),
        'AUTOKOM_FIELD_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'TOKEN_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'AUTOKOM_RUNNER_SHARED_KEY': secrets.token_hex(48),
        'AUTOKOM_CONTROL_SOCKET': '/run/autokom-preprod/platform-control.sock',
        'FRONTEND_URL': 'https://' + HOST, 'ALLOWED_HOSTS': HOST, 'AUTOKOM_BASE_DOMAIN': 'auto.ikom.mg',
        'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg', 'AUTOKOM_SECURITY_REPORTS_ENABLED': 'true',
        'AUTOKOM_SECURITY_FEEDBACK_ENABLED': 'false',
        'AUTOKOM_MVP_ENABLED': 'true',
        'EMAIL_BACKEND': 'django.core.mail.backends.dummy.EmailBackend',
        'AUTOKOM_PREPROD_EMAIL_ENABLED': 'false',
    })
    write_once(ETC / 'runtime.env', {
        **{k: old_runtime.get(k, '') for k in (
            'AUTOKOM_INGRESS_IPV4', 'AUTOKOM_ACME_EMAIL', 'AUTOKOM_JENKINS_URL')},
        'AUTOKOM_JENKINS_JOB': 'autokom-preprod-client-deploy',
        'AUTOKOM_JENKINS_USER': '', 'AUTOKOM_JENKINS_API_TOKEN': '',
        'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg', 'AUTOKOM_PREPROD_ALLOWED_HOSTS': '',
    })
    write_once(ETC / 'cloudflare-dns.env', {
        k: old_dns.get(k, '') for k in ('CLOUDFLARE_ZONE_ID', 'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ZONE_NAME')
    })
    pwd = private(ETC / 'backend.env')['DB_PASSWORD']
    if not re.fullmatch('[a-f0-9]{64}', pwd):
        raise ValueError('Mot de passe initial non conforme.')
    query = f"""
CREATE DATABASE IF NOT EXISTS autokom_preprod CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'autokom_preprod'@'127.0.0.1' IDENTIFIED BY '{pwd}';
GRANT ALL ON autokom_preprod.* TO 'autokom_preprod'@'127.0.0.1';
"""
    subprocess.run(['mysql', '--protocol=socket', '--user=root'], input=query,
                   text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def publish_dns_and_tls():
    from autokom_dns_cloudflare import Cloudflare
    from autokom_nginx_bind_policy import https_listens
    from observability_dns01 import ensure_certbot_credentials, certbot_dns01_args, plugin_available

    dns = private(ETC / 'cloudflare-dns.env')
    runtime = private(ETC / 'runtime.env')
    zone = dns.get('CLOUDFLARE_ZONE_NAME') or 'ikom.mg'
    if zone != 'ikom.mg' or not re.fullmatch(r'[a-f0-9]{32}', dns.get('CLOUDFLARE_ZONE_ID', '')):
        raise ValueError('Zone DNS de préproduction invalide.')
    ip = str(ipaddress.IPv4Address(runtime['AUTOKOM_INGRESS_IPV4']))
    email = runtime['AUTOKOM_ACME_EMAIL']
    if not ipaddress.IPv4Address(ip).is_global or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('IP publique ou contact Certbot absent.')
    if not plugin_available():
        raise ValueError('Plugin Certbot DNS Cloudflare absent.')
    conf = Path('/etc/nginx/sites-available/autokom-preprod.conf')
    link = Path('/etc/nginx/sites-enabled/autokom-preprod.conf')
    marker = '# AUTOKOM PREPROD MANAGED'
    if conf.is_symlink() or (conf.exists() and not conf.read_text().startswith(marker + '\n')):
        raise ValueError('Vhost de préproduction non géré.')
    if (link.exists() or link.is_symlink()) and (not link.is_symlink() or link.resolve() != conf):
        raise ValueError('Lien Nginx de préproduction déjà utilisé.')
    for folder in ('/etc/nginx/sites-enabled', '/etc/nginx/conf.d'):
        for other in Path(folder).glob('*'):
            if other.is_file() and other.resolve() != conf and any(
                    HOST in m.split() for m in re.findall(r'(?m)^\s*server_name\s+([^;]+);', other.read_text(errors='replace'))):
                raise ValueError('Nom pre.auto.ikom.mg déjà servi par un autre vhost.')
    cf = Cloudflare(dns)
    records = [r for r in cf.records(record_name=HOST) if r.get('name') == HOST
               and r.get('type') in ('A', 'AAAA', 'CNAME', 'NS')]
    if records and (len(records) != 1 or records[0].get('type') != 'A'
                    or records[0].get('content') != ip or records[0].get('proxied') is not False
                    or records[0].get('comment') != 'AUTOKOM preprod console'):
        raise ValueError('Enregistrement DNS existant non géré ou cible différente. Rien écrasé.')
    if not records:
        cf.request('POST', f'/zones/{cf.zone_id}/dns_records',
                   data={'type': 'A', 'name': HOST, 'content': ip, 'ttl': 120, 'proxied': False,
                         'comment': 'AUTOKOM preprod console'})
    creds = ensure_certbot_credentials(dns['CLOUDFLARE_API_TOKEN'], folder=ETC / 'acme')
    subprocess.run(['certbot', 'certonly', '--non-interactive', '--agree-tos',
                    '--keep-until-expiring', '--cert-name', HOST, '--email', email,
                    *certbot_dns01_args(HOST, creds)], check=True, timeout=420)
    content = f"""{marker}
server {{
{https_listens()}    server_name {HOST};
    ssl_certificate /etc/letsencrypt/live/{HOST}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{HOST}/privkey.pem;
    root {STATE}/frontend;
    location / {{ try_files $uri $uri/ /index.html; }}
    location /api/ {{
        access_log off;
        client_max_body_size 1m;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 75s;
        proxy_pass http://127.0.0.1:8272;
    }}
    location /static/ {{ alias {STATE}/static/; }}
}}
"""
    lock = os.open('/var/lib/autokom/client-runtime/.deploy.lock',
                   os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    previous = conf.read_bytes() if conf.exists() else None
    linked = link.is_symlink()

    def install(data):
        with tempfile.NamedTemporaryFile(dir=conf.parent, delete=False) as tmp:
            name = Path(tmp.name)
            os.fchmod(tmp.fileno(), 0o644)
            tmp.write(data)
        os.replace(name, conf)

    try:
        install(content.encode())
        if not linked:
            link.symlink_to(conf)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        if not linked:
            link.unlink(missing_ok=True)
        if previous is None:
            conf.unlink(missing_ok=True)
        else:
            install(previous)
        raise
    finally:
        os.close(lock)


def validate_worker():
    runtime = private(ETC / 'runtime.env')
    backend = private(ETC / 'backend.env')
    names = [x.strip() for x in runtime.get('AUTOKOM_PREPROD_ALLOWED_HOSTS', '').split(',') if x.strip()]
    if (not runtime.get('AUTOKOM_JENKINS_USER') or not runtime.get('AUTOKOM_JENKINS_API_TOKEN')
            or runtime.get('AUTOKOM_JENKINS_JOB') != 'autokom-preprod-client-deploy'
            or not backend.get('GITHUB_APP_CLIENT_ID') or not backend.get('GITHUB_APP_CLIENT_SECRET')
            or not backend.get('GITHUB_APP_CALLBACK_URL') or not names
            or any(not re.fullmatch(r'(?:(?:pre|backend)\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auto\.ikom\.mg', x)
                   or x in (HOST, 'backend.auto.ikom.mg') for x in names)):
        raise ValueError('Job, GitHub App ou domaines de démonstration manquants.')
    old = private(Path('/etc/autokom/secrets/runtime.env'))
    prod_backend = private(Path('/etc/autokom/secrets/backend.env'))
    for key in ('DJANGO_SECRET_KEY', 'CLIENT_JWT_SIGNING_KEY', 'AUTOKOM_FIELD_ENCRYPTION_KEY',
                'AUTOKOM_RUNNER_SHARED_KEY', 'TOKEN_ENCRYPTION_KEY', 'GITHUB_WEBHOOK_SECRET'):
        if backend.get(key) and backend.get(key) in {prod_backend.get(key), old.get(key)}:
            raise ValueError('Clé réutilisée depuis la production.')
    if (runtime.get('AUTOKOM_JENKINS_USER') == old.get('AUTOKOM_JENKINS_USER')
            or runtime.get('AUTOKOM_JENKINS_API_TOKEN') == old.get('AUTOKOM_JENKINS_API_TOKEN')):
        raise ValueError('Compte Jenkins dédié obligatoire.')
    from autokom_runtime_admin import jenkins_request
    status, _ = jenkins_request(runtime, 'GET', '/job/autokom-preprod-client-deploy/api/json')
    if status != 200:
        raise ValueError('Le job préproduction est inaccessible au compte Jenkins configuré.')


def publish_assets(source):
    import shutil
    from uuid import uuid4
    src = Path(source)
    # The parent of the builder-owned build/ is the root-owned mktemp dir.
    # Open every untrusted directory relative to an already-open descriptor.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    work_fd = os.open(src.parent.parent, flags)
    try:
        build_fd = os.open(src.parent.name, flags, dir_fd=work_fd)
        try:
            source_fd = os.open(src.name, flags, dir_fd=build_fd)
        finally:
            os.close(build_fd)
    finally:
        os.close(work_fd)
    destination = STATE / 'frontend-releases' / str(uuid4())
    total = count = 0
    try:
        destination.parent.mkdir(mode=0o755, exist_ok=True)
        destination.parent.chmod(0o755)
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
        for folder, dirs, names, fd in os.fwalk('.', dir_fd=source_fd, follow_symlinks=False):
            target = destination / folder
            for name in dirs:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('Lien symbolique dans le build React.')
                (target / name).mkdir(mode=0o755)
                (target / name).chmod(0o755)
            for name in names:
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(file_fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 40 * 1024 * 1024:
                        raise ValueError('Ressource React invalide.')
                    data = stream.read(info.st_size + 1)
                total += len(data)
                count += 1
                if total > 160 * 1024 * 1024 or count > 10000 or len(data) != info.st_size:
                    raise ValueError('Build React trop volumineux ou modifié.')
                (target / name).write_bytes(data)
                (target / name).chmod(0o644)
        if not (destination / 'index.html').is_file():
            raise ValueError('Build React absent ou non conforme.')
        pointer = STATE / 'frontend'
        if pointer.exists() and not pointer.is_symlink():
            raise ValueError('Chemin React non géré.')
        new_pointer = STATE / ('.frontend-link-' + destination.name)
        new_pointer.symlink_to(destination)
        os.replace(new_pointer, pointer)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        os.close(source_fd)


def run_manage(args):
    import pwd
    vals = private(ETC / 'backend.env')
    account = pwd.getpwnam('autokom-preprod')
    os.chdir(ROOT / 'source/platform/backend')
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    python = str(ROOT / 'venv/bin/python')
    os.execve(python, [python, 'manage.py', *args], {
        **vals, 'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
        'DJANGO_SETTINGS_MODULE': 'config.preprod',
    })


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if os.geteuid() != 0:
            raise ValueError('Utiliser sudo depuis le checkout master.')
        action = sys.argv[1]
        if action == 'init':
            init()
        elif action == 'publish':
            publish_dns_and_tls()
        elif action == 'assets':
            publish_assets(sys.argv[2])
        elif action == 'validate-worker':
            validate_worker()
        elif action == 'django':
            run_manage(sys.argv[2:])
        else:
            raise ValueError('Action inconnue.')
    except ValueError as exc:
        if type(exc) is ValueError and exc.args and isinstance(exc.args[0], str) and not isinstance(exc, UnicodeError):
            print('Préproduction : précondition non satisfaite.', file=sys.stderr)
        raise SystemExit('Vérifier les fichiers privés et les prérequis documentés.') from None
    except Exception as exc:
        # No subprocess input, credentials or provider response body.
        raise SystemExit(f'Préproduction : {type(exc).__name__}. Vérifier la configuration privée.') from None
