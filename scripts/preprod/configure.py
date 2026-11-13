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


def write_private(path, values):
    if path.is_symlink():
        raise ValueError('Lien symbolique interdit pour la configuration.')
    fd, name = tempfile.mkstemp(prefix='.autokom-stage-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            for key, value in sorted(values.items()):
                if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or any(c in value for c in ('\n', '\r', '\x00')):
                    raise ValueError('Paramètre de configuration invalide.')
                esc = value.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))
                stream.write(f'{key}="{esc}"\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def stage_backend_identity(values):
    return (values.get('DB_NAME'), values.get('DB_USER'), values.get('DB_HOST'),
            values.get('DB_TEST_NAME'), values.get('AUTOKOM_CONTROL_SOCKET'), values.get('FRONTEND_URL'))


def provision_database(password, reset=False):
    if not re.fullmatch(r'[a-f0-9]{64}', password):
        raise ValueError('Identifiant MySQL préproduction non conforme.')
    sql = f"""CREATE DATABASE IF NOT EXISTS autokom_preprod CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'autokom_preprod'@'127.0.0.1' IDENTIFIED BY '{password}';
"""
    if reset:
        sql += f"ALTER USER 'autokom_preprod'@'127.0.0.1' IDENTIFIED BY '{password}';\n"
    sql += "GRANT ALL ON autokom_preprod.* TO 'autokom_preprod'@'127.0.0.1';\n"
    subprocess.run(['mysql', '--protocol=socket', '--user=root'], input=sql,
                   text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def check_stage_database(password):
    if not re.fullmatch(r'[a-f0-9]{64}', password):
        raise ValueError('Identifiant MySQL préproduction non conforme.')
    with tempfile.NamedTemporaryFile(mode='w', dir=ETC) as options:
        options.write('[client]\nuser=autokom_preprod\nhost=127.0.0.1\npassword='+password+'\n')
        options.flush()
        result = subprocess.run(['mysql', '--defaults-file='+options.name, '--protocol=TCP',
                                 'autokom_preprod', '-Nse', 'SELECT DATABASE()'], text=True,
                                capture_output=True, timeout=15)
    if result.returncode or result.stdout.strip() != 'autokom_preprod':
        raise RuntimeError('MySQL préproduction : authentification ou base dédiée non confirmée.')


def init():
    old_runtime = private(Path('/etc/autokom/secrets/runtime.env'))
    old_dns = private(Path('/etc/autokom/secrets/cloudflare-dns.env'))
    if not ipaddress.IPv4Address(old_runtime['AUTOKOM_INGRESS_IPV4']).is_global:
        raise ValueError('IP publique de la VM principale absente.')
    backend_path = ETC / 'backend.env'
    defaults = {
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
    }
    repair = False
    if backend_path.exists() or backend_path.is_symlink():
        existing = private(backend_path)
        if stage_backend_identity(existing) != stage_backend_identity(defaults):
            prod = private(Path('/etc/autokom/secrets/backend.env'))
            # Repair only the known copied production configuration. Never
            # guess at a custom staging database or migrate through it.
            if not prod.get('DB_NAME') or (existing.get('DB_NAME'), existing.get('DB_USER'),
                    existing.get('DB_PASSWORD')) != (prod.get('DB_NAME'), prod.get('DB_USER'),
                                                    prod.get('DB_PASSWORD')):
                raise RuntimeError('Préproduction : backend.env non isolé et copie de production non reconnue.')
            backup_fd, backup_name = tempfile.mkstemp(prefix='backend.env.production-copy-', dir=ETC)
            with os.fdopen(backup_fd, 'wb') as stream:
                stream.write(backend_path.read_bytes())
            repair = True
        else:
            defaults = existing
    prod_backend = private(Path('/etc/autokom/secrets/backend.env'))
    for key in ('DJANGO_SECRET_KEY', 'CLIENT_JWT_SIGNING_KEY', 'AUTOKOM_FIELD_ENCRYPTION_KEY',
                'AUTOKOM_RUNNER_SHARED_KEY', 'TOKEN_ENCRYPTION_KEY', 'DB_PASSWORD'):
        if not defaults.get(key) or defaults[key] in {prod_backend.get(key), old_runtime.get(key)}:
            raise RuntimeError('Préproduction : secret manquant ou partagé avec la production.')
    provision_database(defaults['DB_PASSWORD'], reset=repair)
    check_stage_database(defaults['DB_PASSWORD'])
    if repair or not backend_path.exists():
        write_private(backend_path, defaults)
    if repair:
        print('Copie de production mise de côté en root:0600. Base, utilisateur et clés de préproduction restaurés.')
    # Optional, separately provisioned GitHub App settings survive recovery.
    git_config = ETC / 'github.env'
    if git_config.exists() or git_config.is_symlink():
        extra = private(git_config)
        permitted = {'GITHUB_APP_CLIENT_ID','GITHUB_APP_CLIENT_SECRET','GITHUB_APP_SLUG',
                     'GITHUB_WEBHOOK_SECRET'}
        callbacks = {'GITHUB_APP_CALLBACK_URL':'https://'+HOST+'/api/projects/git/github/callback/',
                     'GITHUB_AUTH_CALLBACK_URL':'https://'+HOST+'/auth/github/callback'}
        if not all(extra.get(key) for key in permitted - {'GITHUB_WEBHOOK_SECRET'}):
            raise RuntimeError('GitHub App de préproduction incomplète.')
        if extra.get('GITHUB_WEBHOOK_SECRET') and extra['GITHUB_WEBHOOK_SECRET'] == prod_backend.get('GITHUB_WEBHOOK_SECRET'):
            raise RuntimeError('Le secret webhook doit être distinct de celui de production.')
        updated = {**defaults, **{k:v for k,v in extra.items() if k in permitted}, **callbacks}
        if updated != defaults:
            write_private(backend_path, updated)
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


def probe_https():
    from autokom_nginx_bind_policy import ingress_probe_address
    import json
    import time
    address = ingress_probe_address()
    for attempt in range(6):
        result = subprocess.run(['curl','-fsS','--noproxy','*','--max-time','5','--resolve',
            f'{HOST}:443:{address}', f'https://{HOST}/api/health/'], capture_output=True, text=True)
        if result.returncode == 0:
            try:
                body = json.loads(result.stdout)
                if body.get('data', {}).get('status') == 'ok':
                    print('Préproduction · SNI, certificat et API HTTPS vérifiés.')
                    return
            except (ValueError, AttributeError):
                pass
        if attempt < 5:
            time.sleep(1)
    raise RuntimeError('Préproduction : échec du test HTTPS sur l’adresse d’ingress (' + address +
                       '). Vérifier Nginx et le journal autokom-pre app logs.')


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
        raise RuntimeError('Préproduction : plugin Certbot DNS Cloudflare absent.')
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


def worker_configured():
    runtime = private(ETC / 'runtime.env')
    backend = private(ETC / 'backend.env')
    return all(runtime.get(k) for k in ('AUTOKOM_JENKINS_USER','AUTOKOM_JENKINS_API_TOKEN',
                                        'AUTOKOM_PREPROD_ALLOWED_HOSTS')) and all(
        backend.get(k) for k in ('GITHUB_APP_CLIENT_ID','GITHUB_APP_CLIENT_SECRET',
                                 'GITHUB_APP_SLUG','GITHUB_APP_CALLBACK_URL'))


def connect():
    import getpass
    if not sys.stdin.isatty():
        raise RuntimeError('Connexion initiale à exécuter dans un terminal.')
    runtime_path = ETC / 'runtime.env'
    runtime = private(runtime_path)
    git_path = ETC / 'github.env'
    git = private(git_path) if git_path.exists() else {}
    print('Paramètres dédiés de préproduction. Les jetons sont saisis sans affichage.')
    def required(label, old='', secret=False):
        prompt = label + (' (Entrée : conserver)' if old else '') + ' : '
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip() or old
        if not value:
            raise RuntimeError('Configuration non enregistrée : valeur requise absente.')
        return value
    for key, label in (('GITHUB_APP_CLIENT_ID','Client ID GitHub App'),('GITHUB_APP_SLUG','Slug GitHub App')):
        git[key] = required(label, git.get(key,''))
    git['GITHUB_APP_CLIENT_SECRET'] = required('Client secret GitHub App', git.get('GITHUB_APP_CLIENT_SECRET',''), True)
    for key, label, secret in (
            ('AUTOKOM_JENKINS_USER','Compte Jenkins préproduction',False),
            ('AUTOKOM_JENKINS_API_TOKEN','Jeton Jenkins préproduction',True),
            ('AUTOKOM_PREPROD_ALLOWED_HOSTS','Domaines clients séparés par des virgules',False)):
        runtime[key] = required(label, runtime.get(key,''), secret)
    prod = private(Path('/etc/autokom/secrets/runtime.env'))
    if any(runtime[k] == prod.get(k) for k in ('AUTOKOM_JENKINS_USER','AUTOKOM_JENKINS_API_TOKEN')):
        raise RuntimeError('Le compte Jenkins de production ne peut pas être réutilisé.')
    admin_user = input('Compte Jenkins avec droits de création (Entrée : réutiliser le compte dédié) : ').strip()
    if admin_user:
        admin = {'AUTOKOM_JENKINS_USER':admin_user,
                 'AUTOKOM_JENKINS_API_TOKEN':required('Jeton API Jenkins de provisioning', secret=True)}
        write_private(ETC / 'jenkins-admin.env', admin)
    write_private(git_path, git)
    write_private(runtime_path, runtime)
    print('Connexions préproduction enregistrées. La CLI configure les callbacks côté AUTOKOM.')


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
        elif action == 'busy':
            run_manage(['shell', '-c', 'from apps.deployments.runs.models import DeploymentRun, StackDeployment; import sys; sys.exit(5 if DeploymentRun.objects.filter(status__in=["QUEUED","RUNNING","PREPARED"]).exists() or StackDeployment.objects.filter(status__in=["QUEUED","PREPARING","ACTIVATING"]).exists() else 0)'])
        elif action == 'probe-https':
            probe_https()
        elif action == 'publish':
            publish_dns_and_tls()
        elif action == 'assets':
            publish_assets(sys.argv[2])
        elif action == 'validate-worker':
            validate_worker()
        elif action == 'worker-configured':
            raise SystemExit(0 if worker_configured() else 4)
        elif action == 'connect':
            connect()
        elif action == 'django':
            run_manage(sys.argv[2:])
        else:
            raise ValueError('Action inconnue.')
    except RuntimeError as exc:
        # Our RuntimeError messages above contain only fixed diagnostics.
        if type(exc) is RuntimeError:
            raise SystemExit(str(exc)) from None
        raise SystemExit('Configuration privée invalide.') from None
    except ValueError as exc:
        if type(exc) is ValueError and exc.args and isinstance(exc.args[0], str) and not isinstance(exc, UnicodeError):
            print('Préproduction : précondition non satisfaite.', file=sys.stderr)
        raise SystemExit('Vérifier les fichiers privés et les prérequis documentés.') from None
    except Exception as exc:
        # No subprocess input, credentials or provider response body.
        raise SystemExit(f'Préproduction : {type(exc).__name__}. Vérifier la configuration privée.') from None
