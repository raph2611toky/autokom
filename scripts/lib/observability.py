#!/usr/bin/env python3
"""AUTOKOM observability: local-only Prometheus + Grafana + Jenkins exporter.

Designed for Ubuntu 26.04, apt/systemd installations on one VM. Conservative:
never overwrite unknown vhosts, never reset existing credentials, no WAN bind,
no firewall changes, and --check has no side effects. Root required for apply.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import getpass
import grp
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import urlsplit
from observability_config import Settings, ConfigError, load as load_settings, validate as validate_settings
from tool_banner import render as autokom_banner, first_install, section, Activity
from jenkins_network import private_listener
from domain_validation import DomainRoutingError, check_direct_ec2_domain, check_http01_local

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

NGINX_AVAIL = Path('/etc/nginx/sites-available')
NGINX_ENABLED = Path('/etc/nginx/sites-enabled')
PROM_CONFIG = Path('/etc/prometheus/prometheus.yml')
PROM_DROPIN = Path('/etc/systemd/system/prometheus.service.d/90-autokom.conf')
GRAFANA_DROPIN = Path('/etc/systemd/system/grafana-server.service.d/90-autokom.conf')
GRAFANA_DS = Path('/etc/grafana/provisioning/datasources/autokom-prometheus.yaml')
SECRETS = Path('/etc/autokom/secrets')
PROM_HTPASSWD = Path('/etc/nginx/.autokom-prometheus.htpasswd')
CONFIG_STATE = Path('/etc/autokom/state/observability.json')
TIMEOUT = 20

# Validated once at startup; functions also accept Settings in unit tests.
SETTINGS = Settings()

def cfg():
    return SETTINGS


def previous_state():
    if not CONFIG_STATE.exists():
        return {}
    if CONFIG_STATE.is_symlink():
        raise SetupError('État AUTOKOM symbolique non autorisé')
    try:
        record = json.loads(CONFIG_STATE.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise SetupError('État AUTOKOM illisible : ' + str(CONFIG_STATE)) from exc
    if not isinstance(record, dict) or record.get('schema') != 1:
        raise SetupError('Version d’état AUTOKOM inconnue : intervention manuelle requise')
    return record


def save_state():
    settings = cfg()
    record = {
        'schema': 1,
        'jenkins_port': settings.jenkins_port,
        'jenkins_metrics_path': settings.jenkins_metrics_path,
        'jenkins_job': settings.jenkins_job,
        'prometheus_port': settings.prometheus_port,
        'grafana_port': settings.grafana_port,
        'prometheus_storage_path': settings.storage_path,
        'grafana_datasource_uid': settings.datasource_uid,
    }
    owned_write(CONFIG_STATE, json.dumps(record, sort_keys=True, indent=2) + '\n', mode=0o600)



class SetupError(RuntimeError):
    pass


def out(icon, message):
    color = {'✓': '32', '+': '36', '›': '36', '!': '33', '✗': '31'}.get(icon, '0')
    prefix = f'\033[{color}m{icon}\033[0m' if sys.stdout.isatty() and not os.getenv('NO_COLOR') else icon
    print(f'  {prefix} {message}', flush=True)


def run(args, *, input_data=None, timeout=120, allowed=(0,)):
    """Execute commands with 3 ephemeral green activity lines on an interactive TTY.

    Actual sub-command stdout/stderr is captured (never dumps passwords/headers).
    The live panel shows only observed command and elapsed time; it does not
    manufacture fake dpkg messages or flood the persistent terminal history.
    """
    show = (sys.stdout.isatty() and not os.environ.get('NO_COLOR') and
            os.environ.get('AUTOKOM_LIVE', '1') != '0')
    child_env = os.environ.copy()
    if args and args[0] == 'apt-get':
        child_env.update(DEBIAN_FRONTEND='noninteractive', APT_LISTCHANGES_FRONTEND='none')
        args = list(args)
        args[1:1] = ['-o','DPkg::Lock::Timeout=60']
    if not show:
        try:
            p = subprocess.run(args, input=input_data, capture_output=True, text=True,
                               timeout=timeout, check=False, env=child_env)
        except subprocess.TimeoutExpired as exc:
            raise SetupError(f'{args[0]} a dépassé {timeout}s. Examiner les verrous dpkg et les sources APT ; aucun succès présumé.') from exc
    else:
        import itertools
        import time as clock
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env)
        start = clock.monotonic()
        shown = False
        spinner = itertools.cycle('⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏')
        try:
            while True:
                try:
                    stdout, stderr = proc.communicate(input=input_data, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    input_data = None
                    elapsed = int(clock.monotonic() - start)
                    if elapsed < 1:
                        continue
                    if not shown:
                        sys.stdout.write('\n\n\n')
                        shown = True
                    label = {'apt-get':'Gestion des paquets', 'systemctl':'Configuration des services',
                             'certbot':'Certificat TLS', 'nginx':'Vérification Nginx'}.get(args[0], 'Exécution')
                    lines = [f'  \033[32m{next(spinner)} {label}\033[0m',
                             '  \033[32m↳ Opération en cours — détails capturés pour diagnostic\033[0m',
                             f'  \033[32m↳ {elapsed//60:02d}:{elapsed%60:02d} écoulées\033[0m']
                    sys.stdout.write('\033[3A')
                    for line in lines:
                        sys.stdout.write('\r\033[2K' + line + '\n')
                    sys.stdout.flush()
                    if elapsed >= timeout:
                        raise SetupError(f'{args[0]} a dépassé {timeout}s ; vérifier journalctl et verrous dpkg.')
            p = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        except Exception:
            proc.kill()
            proc.communicate()
            raise
        finally:
            if shown:
                sys.stdout.write('\033[3A' + ('\r\033[2K\n' * 3) + '\033[3A')
                sys.stdout.flush()
    if p.returncode not in allowed:
        raise SetupError(f'{args[0]} a échoué (code {p.returncode}) : {(p.stderr or p.stdout)[-400:]}')
    return p.stdout


def active(unit):
    return subprocess.run(['systemctl', 'is-active', '--quiet', unit],
                          capture_output=True).returncode == 0


def package_installed(name):
    return subprocess.run(['dpkg-query', '-W', '-f=${Status}', name],
                          capture_output=True, text=True).stdout.strip() == 'install ok installed'


def prompt_choice(title, labels, default=1, hint='', noninteractive=False):
    if noninteractive:
        return default
    from menu import pick
    return pick(title, labels, default=default, hint=hint)


def prompt_text(title, default='', hint='', noninteractive=False):
    if noninteractive:
        return default
    from menu import ask
    return ask(title, default=default, hint=hint)


def secret(prompt, noninteractive=False):
    if noninteractive:
        raise SetupError('Secret requis : exécuter en terminal interactif (aucun mot de passe accepté en argument)')
    try:
        result = getpass.getpass('  ' + prompt + ': ')
    except (EOFError, OSError) as e:
        raise SetupError('Terminal /dev/tty nécessaire pour une saisie masquée') from e
    if '\n' in result or '\r' in result or not result:
        raise SetupError('Mot de passe invalide')
    return result


def password_twice(name, noninteractive=False):
    a = secret(f'{name} — mot de passe (16 caractères minimum)', noninteractive)
    b = secret('Confirmer le mot de passe', noninteractive)
    if len(a) < 16 or a != b:
        raise SetupError('Mot de passe trop court ou confirmation incorrecte')
    return a


def yesno(question, default=False, *, noninteractive=False):
    return prompt_choice(question, ['Non', 'Oui'], 2 if default else 1,
                         noninteractive=noninteractive) == 2


def validate_domain(value):
    value = value.strip().lower().rstrip('.')
    if not value:
        return ''
    if len(value) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value):
        raise SetupError(f'Domaine invalide : {value!r}')
    # Intentionally DNS-only; no IP literals, wildcard, URL, path or schemes.
    return value


def resolve_domain(domain):
    try:
        ips = {x[4][0] for x in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as e:
        raise SetupError(f'DNS introuvable pour {domain} : {e}') from e
    if not any(ipaddress.ip_address(ip).is_global for ip in ips):
        raise SetupError(f'{domain} ne résout pas vers une IP publique : {sorted(ips)}')
    return sorted(ips)


def get_local(url, *, username=None, password=None, method='GET', data=None, timeout=TIMEOUT):
    parsed = urlsplit(url)
    approved = {cfg().jenkins_port, cfg().prometheus_port, cfg().grafana_port}
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
            parsed.port not in approved or not parsed.path.startswith('/') or
            parsed.username is not None or parsed.password is not None or
            parsed.fragment):
        raise SetupError('Requête HTTP interne refusée : URL hors ports AUTOKOM/localhost')
    headers = {'User-Agent': 'AUTOKOM/observability', 'Accept': 'application/json'}
    if username is not None:
        encoded = base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')
        headers['Authorization'] = f'Basic {encoded}'
    if data is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None
    # Never send Basic credentials through an HTTP redirect, even one emitted
    # by an otherwise local service.
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as res:
            return res.status, res.read(131072).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0, ''


def listener(port):
    text = run(['ss', '-H', '-ltn'], allowed=(0,))
    addresses = []
    for row in text.splitlines():
        cols = row.split()
        if len(cols) > 3 and cols[3].endswith(':' + str(port)):
            addresses.append(cols[3])
    return addresses


def assert_private(port):
    addresses = listener(port)
    if not addresses:
        raise SetupError(f'Aucun service sur le port {port}')
    if not private_listener(addresses):
        raise SetupError(f'Port {port} NON PRIVÉ : {", ".join(addresses)}; installation interrompue')
    out('✓', f'TCP/{port} limité à loopback : {", ".join(addresses)}')


def owned_write(path, content, *, mode=0o644, group=None):
    """Atomically write managed file, never replace symlink; preserve on no change."""
    path = Path(path)
    if path.is_symlink():
        raise SetupError(f'Lien symbolique interdit : {path}')
    old = path.read_text(encoding='utf-8') if path.is_file() else None
    if old == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_file():
        raise SetupError(f'Fichier cible non régulier : {path}')
    if old is not None:
        backup = path.with_name(path.name + '.autokom.bak')
        if not backup.exists():
            shutil.copy2(path, backup)
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix='.autokom-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            os.fchmod(f.fileno(), mode)
            if group:
                os.fchown(f.fileno(), 0, grp.getgrnam(group).gr_gid)
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def validate_prom_config():
    run(['promtool', 'check', 'config', str(PROM_CONFIG)], timeout=40)


def prom_scrape_config(auth_user=None, auth_path=None):
    settings = cfg()
    # This installer owns its new Prometheus config on fresh instances only.
    config = [
        'global:', f'  scrape_interval: {settings.scrape_interval}', f'  evaluation_interval: {settings.scrape_interval}',
        'scrape_configs:',
        '  - job_name: prometheus',
        '    static_configs:', f"      - targets: ['{settings.prometheus_host}:{settings.prometheus_port}']",
        f'  - job_name: {settings.jenkins_job}', f'    metrics_path: {settings.jenkins_metrics_path}',
        f'    scrape_interval: {settings.scrape_interval}',
        '    static_configs:', f"      - targets: ['{settings.jenkins_host}:{settings.jenkins_port}']"
    ]
    if auth_user:
        config += ['    basic_auth:', f'      username: {json.dumps(auth_user)}',
                   f'      password_file: {json.dumps(str(auth_path))}']
    return '\n'.join(config) + '\n'


def merge_prom_config(text, auth_user=None, auth_path=None):
    """Reconcile only the AUTOKOM-compatible scrape job; preserve unrelated jobs.

    For older releases, the single localhost Jenkins scrape on port 8080 is
    recognized as AUTOKOM-compatible. Conflicts/complex jobs are rejected.
    """
    try:
        import yaml
    except ImportError as e:
        raise SetupError('PyYAML requis pour fusionner une configuration Prometheus existante') from e
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get('scrape_configs'), list):
        raise SetupError('Configuration Prometheus existante non standard : fusion refusée')
    settings = cfg()
    previous_state_data = previous_state()
    old_job_name = previous_state_data.get('jenkins_job', 'jenkins')
    target = f'{settings.jenkins_host}:{settings.jenkins_port}'
    desired_job = {'job_name': settings.jenkins_job,
                   'metrics_path': settings.jenkins_metrics_path,
                   'scrape_interval': settings.scrape_interval,
                   'static_configs': [{'targets': [target]}]}
    if auth_user:
        desired_job['basic_auth'] = {'username': auth_user, 'password_file': str(auth_path)}
    jobs = parsed['scrape_configs']
    matches = [i for i, job in enumerate(jobs)
               if isinstance(job, dict) and job.get('job_name') in {settings.jenkins_job, old_job_name}]
    if len(matches) > 1:
        raise SetupError(f'Plusieurs jobs Prometheus {settings.jenkins_job} : conflit')
    if matches:
        idx = matches[0]
        previous = jobs[idx]
        # Do not replace third-party jobs or credentials on an existing cluster.
        legacy_target = previous.get('static_configs', [])
        allowed_targets = [[target], ['127.0.0.1:8080'],
                           [f"127.0.0.1:{previous_state_data.get('jenkins_port',8080)}"]]
        if (len(legacy_target) != 1 or not isinstance(legacy_target[0], dict) or
            legacy_target[0].get('targets') not in allowed_targets or
            previous.get('metrics_path', '/metrics') not in
                {settings.jenkins_metrics_path, '/prometheus/',
                 previous_state_data.get('jenkins_metrics_path', '/prometheus/')} or
            any(key not in {'job_name','metrics_path','scrape_interval','static_configs','basic_auth'}
                for key in previous)):
            raise SetupError(f'Job {settings.jenkins_job} personnalisé : migration automatique refusée')
        # Preserve existing authorization unless caller explicitly provides replacement.
        if not auth_user and 'basic_auth' in previous:
            desired_job['basic_auth'] = previous['basic_auth']
        if previous == desired_job:
            out('✓', f'Job {settings.jenkins_job} déjà conforme')
        else:
            jobs[idx] = desired_job
    else:
        jobs.append(desired_job)
    # Reconcile self-scrape if it matches the AUTOKOM default exactly.
    for job in jobs:
        if not isinstance(job, dict) or job.get('job_name') != 'prometheus':
            continue
        static = job.get('static_configs', [])
        previous_port = previous_state_data.get('prometheus_port', 9090)
        if (len(static) == 1 and isinstance(static[0], dict) and
            static[0].get('targets') in ([f'127.0.0.1:{previous_port}'],
                                         [f'localhost:{previous_port}'],
                                         [f'{settings.prometheus_host}:{settings.prometheus_port}'])):
            static[0]['targets'] = [f'{settings.prometheus_host}:{settings.prometheus_port}']
    transformed = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True)
    # Preserve comments/formatting in existing Prometheus YAML when no change is needed.
    if yaml.safe_load(text) == parsed:
        return text
    return transformed

def unit_override_prometheus():
    settings = cfg()
    return ('# AUTOKOM managed; Debian/Ubuntu data path retained\n'
            '[Service]\nExecStart=\nExecStart=/usr/bin/prometheus '
            '--config.file=/etc/prometheus/prometheus.yml '
            f'--storage.tsdb.path={settings.storage_path} '
            f'--web.listen-address={settings.prometheus_host}:{settings.prometheus_port} '
            + (f'--web.external-url=https://{settings.prometheus_domain}/ ' if settings.prometheus_domain else '')
            + f'--storage.tsdb.retention.time={settings.retention}\n')

def unit_override_grafana(domain):
    settings = cfg()
    result = ('# AUTOKOM managed: never bind Grafana to WAN\n[Service]\n'
              f'Environment="GF_SERVER_HTTP_ADDR={settings.grafana_host}"\n'
              f'Environment="GF_SERVER_HTTP_PORT={settings.grafana_port}"\n'
              'Environment="GF_USERS_ALLOW_SIGN_UP=false"\n'
              'Environment="GF_SECURITY_DISABLE_GRAVATAR=true"\n')
    if domain:
        result += (f'Environment="GF_SERVER_DOMAIN={domain}"\n'
                   f'Environment="GF_SERVER_ROOT_URL=https://{domain}/"\n'
                   'Environment="GF_SECURITY_COOKIE_SECURE=true"\n')
    return result


def datasource_yaml():
    settings = cfg()
    return ('# AUTOKOM Grafana datasource — localhost only\n'
            'apiVersion: 1\n'
            'datasources:\n'
            f'  - name: {settings.datasource_name}\n'
            f'    uid: {settings.datasource_uid}\n'
            '    type: prometheus\n'
            '    access: proxy\n'
            f'    url: {settings.prometheus_base}\n'
            '    editable: false\n'
            '    jsonData:\n'
            '      httpMethod: POST\n'
            f'      timeInterval: {settings.scrape_interval}\n')

def existing_vhosts(domain):
    if not domain:
        return []
    p = subprocess.run(['nginx', '-T'], capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise SetupError('nginx -T non valide : corriger Nginx avant de gérer un domaine')
    servers = []
    try:
        from nginx_discovery import blocks
    except ImportError:
        # Do not proceed if the safe parser is missing from the current repo.
        raise SetupError('Module nginx_discovery.py requis pour contrôler les domaines')
    for section in blocks(p.stdout, 'server'):
        for raw in re.findall(r'(?m)^\s*server_name\s+([^;]+);', section):
            if domain in raw.lower().split():
                servers.append(section)
    return servers


def vhost_target_ok(vhosts, port):
    return any(f'127.0.0.1:{port}' in v or f'localhost:{port}' in v for v in vhosts)


def valid_cert(domain):
    fullchain = Path('/etc/letsencrypt/live') / domain / 'fullchain.pem'
    if not fullchain.is_file():
        return False
    return (subprocess.run(['openssl', 'x509', '-in', str(fullchain),
                            '-noout', '-checkend', '604800'], capture_output=True).returncode == 0 and
            subprocess.run(['openssl', 'x509', '-in', str(fullchain),
                            '-noout', '-checkhost', domain], capture_output=True).returncode == 0)


def vhost_content(domain, port, *, prometheus=False, gated=True):
    """Start HTTP gated, then publish HTTPS only after certonly succeeds."""
    if gated:
        return (f'# AUTOKOM MANAGED {domain} : {port} — TLS PENDING\n'
                'server {\n    listen 80;\n'
                f'    server_name {domain};\n'
                '    location / { return 503; }\n}\n')
    auth = ('        auth_basic "AUTOKOM Metrics";\n'
            '        auth_basic_user_file /etc/nginx/.autokom-prometheus.htpasswd;\n') if prometheus else ''
    return (f'# AUTOKOM MANAGED {domain} : {port} — HTTPS\n'
            'server {\n'
            '    listen 80;\n'
            f'    server_name {domain};\n'
            '    return 301 https://$host$request_uri;\n'
            '}\n'
            'server {\n'
            '    listen 443 ssl;\n'
            f'    server_name {domain};\n'
            f'    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;\n'
            f'    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;\n'
            '    ssl_protocols TLSv1.2 TLSv1.3;\n'
            '    ssl_session_tickets off;\n'
            '    location / {\n' + auth +
            f'        proxy_pass http://127.0.0.1:{port};\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header Host $host;\n'
            '        proxy_set_header X-Real-IP $remote_addr;\n'
            '        proxy_set_header X-Forwarded-Proto $scheme;\n'
            '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
            '        proxy_set_header Upgrade $http_upgrade;\n'
            '        proxy_set_header Connection "upgrade";\n'
            '    }\n'
            '}\n')


def ensure_prom_basic_auth(username, passwd):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{2,39}', username):
        raise SetupError('Nom utilisateur Prometheus invalide')
    # Nginx supports crypt() hashes. OpenSSL SHA-512 crypt avoids installing
    # apache2-utils (which previously hung before provisioning); secret via stdin.
    # /etc/nginx file stays root-owned, group www-data, mode 0640.
    path = PROM_HTPASSWD
    if path.exists():
        raise SetupError('Identifiants Prometheus déjà présents : aucune réinitialisation automatique')
    try:
        p = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=passwd + '\n',
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError('OpenSSL indisponible pour la protection HTTP Prometheus') from exc
    hashed = p.stdout.strip()
    if p.returncode or not hashed.startswith('$6$') or '\n' in hashed or ':' in hashed:
        raise SetupError('Impossible de générer le hash SHA-512 crypt Prometheus')
    changed = owned_write(path, username + ':' + hashed + '\n', mode=0o640, group='www-data')
    return changed


def configure_nginx(domain, port, kind, args):
    if not domain:
        out('✓', f'{kind} reste local ; aucun domaine ni virtual host créé')
        return
    existing = existing_vhosts(domain)
    managed = NGINX_AVAIL / f'autokom-{kind}-{domain}.conf'
    if existing:
        if not vhost_target_ok(existing, port):
            # Only update an AUTOKOM-owned vhost; never rewrite another service.
            enabled_link = NGINX_ENABLED / managed.name
            if (not managed.is_file() or managed.is_symlink() or
                    not enabled_link.is_symlink() or enabled_link.resolve() != managed or
                    f'# AUTOKOM MANAGED {domain} :' not in managed.read_text(encoding='utf-8')):
                raise SetupError(f'{domain} est déjà utilisé (proxy non géré par AUTOKOM) : intervention refusée')
            if not valid_cert(domain):
                raise SetupError(f'{domain}: certificat existant invalide, correction automatique refusée')
            if args.mode == 'check':
                out('+', f'{domain}: adapter le proxy AUTOKOM au port {port}, après confirmation')
                return
            if not yesno(f'Mettre à jour le proxy AUTOKOM {domain} vers TCP/{port} ?',
                         False, noninteractive=args.non_interactive):
                raise SetupError('Modification de proxy non autorisée')
            old = managed.read_text(encoding='utf-8')
            try:
                owned_write(managed, vhost_content(domain, port,
                                                   prometheus=(kind == 'prometheus'), gated=False))
                run(['nginx', '-t'])
                run(['systemctl', 'reload', 'nginx'])
            except Exception:
                owned_write(managed, old)
                run(['nginx', '-t'])
                run(['systemctl', 'reload', 'nginx'])
                raise
            out('✓', f'{domain}: proxy AUTOKOM mis à jour vers TCP/{port}')
            return
        if not valid_cert(domain):
            raise SetupError(f'{domain} est déjà configuré mais son TLS n’est pas valide : intervention manuelle requise')
        if kind == 'prometheus' and not any('auth_basic_user_file' in block and 'auth_basic ' in block for block in existing):
            raise SetupError('Proxy Prometheus existant sans authentification HTTP : correction manuelle exigée')
        out('✓', f'{domain} : virtual host et certificat existants réutilisés')
        return
    ips = resolve_domain(domain)
    out('›', f'DNS {domain} → {", ".join(ips)} ; Certbot vérifiera l’accès public HTTP-01')
    if args.mode == 'check':
        out('+', f'Créer le proxy HTTPS sécurisé : {domain} → 127.0.0.1:{port}')
        return
    if managed.exists():
        raise SetupError(f'Un fichier Nginx AUTOKOM préexiste mais n’est pas actif : {managed}')
    if not args.email or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.email):
        raise SetupError('Adresse e-mail Let’s Encrypt valide obligatoire pour un nouveau domaine')
    link = NGINX_ENABLED / managed.name
    try:
        # Stage gated HTTP-only site: never expose Grafana credentials on HTTP.
        owned_write(managed, vhost_content(domain, port, prometheus=(kind == 'prometheus'), gated=True))
        link.symlink_to(managed)
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        # Certbot only obtains a certificate and temporarily configures nginx
        # for HTTP-01. AUTOKOM owns the final HTTPS reverse proxy configuration.
        run(['certbot', 'certonly', '--nginx', '--non-interactive',
             '--agree-tos', '--email', args.email, '-d', domain], timeout=300)
        if not valid_cert(domain):
            raise SetupError(f'Certificat de {domain} non confirmé')
        owned_write(managed, vhost_content(domain, port,
                                          prometheus=(kind == 'prometheus'), gated=False))
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        # Certbot renewal already runs automatically on a systemd timer; reload
        # nginx on successful renewals to pick up replaced certificate files.
        hook = Path('/etc/letsencrypt/renewal-hooks/deploy/autokom-reload-nginx.sh')
        if not hook.exists():
            owned_write(hook, '#!/bin/sh\n/usr/sbin/nginx -t && /bin/systemctl reload nginx\n', mode=0o755)
        out('✓', f'{kind} HTTPS activé : https://{domain}/')
    except Exception:
        # If setup fails, restore 503 gate; never leave a public HTTP proxy.
        if managed.exists():
            try:
                owned_write(managed, vhost_content(domain, port,
                                                  prometheus=(kind == 'prometheus'), gated=True))
                run(['nginx', '-t'])
                run(['systemctl', 'reload', 'nginx'])
            except Exception:
                out('!', 'Restauration Nginx non confirmée : exécuter nginx -t immédiatement')
        raise


def ensure_prometheus(args, *, auth_user=None, auth_password=None):
    exists = package_installed('prometheus')
    if first_install(args.mode, exists):
        autokom_banner('prometheus', initial_install=True)
    out('›', f'Prometheus : {"déjà installé" if exists else "absent"}')
    if args.mode == 'check':
        out('+', f'Verifier/installer Prometheus avec écoute locale {cfg().prometheus_base}')
        return
    if exists and PROM_DROPIN.exists() and 'AUTOKOM managed' not in PROM_DROPIN.read_text():
        raise SetupError('Override Prometheus non géré par AUTOKOM : refus')
    if exists and PROM_DROPIN.exists():
        previous = re.search(r'--storage\.tsdb\.path=([^ \n]+)', PROM_DROPIN.read_text())
        if previous and previous.group(1) != cfg().storage_path:
            raise SetupError('Chemin de stockage Prometheus modifié : migration TSDB manuelle exigée '
                             f'({previous.group(1)} -> {cfg().storage_path})')
    if exists and not PROM_DROPIN.exists() and active('prometheus'):
        # Existing deployments may have non-standard args and storage paths.
        # Do not replace their ExecStart without explicit review.
        unit = run(['systemctl', 'cat', 'prometheus'])
        if '/usr/bin/prometheus $ARGS' not in unit:
            raise SetupError('Prometheus déjà actif avec unité personnalisée : ne pas remplacer les options de stockage')
    if exists and active('prometheus'):
        override_changed = not PROM_DROPIN.exists() or PROM_DROPIN.read_text() != unit_override_prometheus()
        config_text = PROM_CONFIG.read_text(encoding='utf-8') if PROM_CONFIG.is_file() else ''
        job_pattern = r'^\s*-\s*job_name:\s*[\"\']?' + re.escape(cfg().jenkins_job) + r'[\"\']?\s*$'
        needs_job = not re.search(job_pattern, config_text, flags=re.M)
        if (override_changed or needs_job) and not yesno('Prometheus existant : confirmer une mise à jour et son redémarrage ?', False, noninteractive=args.non_interactive):
            raise SetupError('Prometheus existant conservé : modification non autorisée')
    # Prestage drop-in BEFORE apt starts the service, to prevent 0.0.0.0 bind.
    changed_override = owned_write(PROM_DROPIN, unit_override_prometheus())
    run(['systemctl', 'daemon-reload'])
    if not exists:
        run(['apt-get', 'update', '-qq'], timeout=240)
        run(['apt-get', 'install', '-y', '--no-install-recommends', 'prometheus', 'promtool'], timeout=420)
    auth_path = None
    if auth_user and auth_password:
        auth_path = Path('/etc/prometheus/autokom-jenkins-token')
        owned_write(auth_path, auth_password + '\n', mode=0o640, group='prometheus')
    if not PROM_CONFIG.is_file():
        owned_write(PROM_CONFIG, prom_scrape_config(auth_user, auth_path))
        changed = True
    else:
        text = PROM_CONFIG.read_text(encoding='utf-8')
        if not text.strip():
            changed = owned_write(PROM_CONFIG, prom_scrape_config(auth_user, auth_path))
        else:
            changed = owned_write(PROM_CONFIG, merge_prom_config(text, auth_user, auth_path))
    validate_prom_config()
    if changed or changed_override or not active('prometheus'):
        run(['systemctl', 'restart', 'prometheus'], timeout=120)
    else:
        run(['systemctl', 'start', 'prometheus'])
    assert_private(cfg().prometheus_port)
    status, _ = get_local(cfg().prometheus_base + '/-/ready')
    if status != 200:
        raise SetupError(f'Prometheus installé mais /-/ready retourne {status}')
    out('✓', 'Prometheus fonctionnel, scraping Jenkins configuré')


def grafana_api(user, pwd, path, method='GET', data=None):
    return get_local(cfg().grafana_base + path, username=user, password=pwd,
                     method=method, data=data)


def grafana_credentials(is_fresh, args):
    """Verify/rotate admin through Grafana local HTTP API; no passwords on argv or disk."""
    if args.mode == 'check':
        out('›', 'Grafana : authentification à configurer/contrôler après installation')
        return
    if is_fresh:
        username = args.grafana_user or prompt_text('Identifiant Grafana', default='admin', noninteractive=args.non_interactive)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{2,39}', username):
            raise SetupError('Identifiant Grafana invalide')
        new_password = password_twice('Grafana', noninteractive=args.non_interactive)
        code, _ = grafana_api('admin', 'admin', '/api/user')
        if code != 200:
            raise SetupError('Compte initial Grafana admin/admin indisponible : ne pas réinitialiser automatiquement')
        status, _ = grafana_api('admin', 'admin', '/api/user/password', 'PUT',
                                {'oldPassword': 'admin', 'newPassword': new_password})
        if status != 200:
            raise SetupError(f'Changement de mot de passe Grafana non confirmé : HTTP {status}')
        if username != 'admin':
            status, original_user = grafana_api('admin', new_password, '/api/user')
            if status != 200:
                raise SetupError('Impossible de lire le profil après création du mot de passe')
            original = json.loads(original_user)
            status, _ = grafana_api('admin', new_password, '/api/users/1', 'PUT',
                                    {'name': original.get('name') or username,
                                     'login': username, 'email': original.get('email') or ''})
            if status != 200:
                raise SetupError(f'Mot de passe modifié, mais changement de nom a échoué : HTTP {status}')
        status, _ = grafana_api(username, new_password, '/api/user')
        if status != 200:
            raise SetupError('Identifiants Grafana finaux non vérifiés')
        out('✓', f'Administrateur Grafana créé et testé : {username}')
        return username, new_password
    # Preserve existing users; offer optional change only with old credentials.
    if not args.non_interactive and yesno('Changer les identifiants de Grafana existant ?', False):
        username = prompt_text('Utilisateur Grafana actuel', default='admin')
        old = secret('Mot de passe Grafana actuel')
        code, _ = grafana_api(username, old, '/api/user')
        if code != 200:
            raise SetupError('Identifiant Grafana existant incorrect; rien n’a été modifié')
        new = password_twice('Nouveau mot de passe Grafana')
        code, _ = grafana_api(username, old, '/api/user/password', 'PUT',
                              {'oldPassword': old, 'newPassword': new})
        if code != 200 or grafana_api(username, new, '/api/user')[0] != 200:
            raise SetupError('Échec de vérification du nouveau mot de passe Grafana')
        out('✓', 'Mot de passe Grafana modifié et validé')
        return username, new
    else:
        out('✓', 'Comptes Grafana existants conservés')
        return None


def verify_grafana_datasource(username: str, password: str, attempts: int = 5):
    """Confirme la datasource par API après le provisioning, sans afficher de secret."""
    with Activity('Vérification de la datasource Grafana', delay=0.4):
        for attempt in range(attempts):
            status, raw = grafana_api(username, password,
                                      '/api/datasources/uid/' + cfg().datasource_uid)
            if status == 200:
                try:
                    ds = json.loads(raw)
                except (ValueError, TypeError) as error:
                    raise SetupError('Grafana API : réponse datasource invalide') from error
                if (not isinstance(ds, dict) or ds.get('type') != 'prometheus'
                        or ds.get('url') != cfg().prometheus_base):
                    raise SetupError('Grafana API : type ou URL de la source non conforme')
                break
            if attempt < attempts - 1:
                time.sleep(2)
        else:
            raise SetupError('Grafana API : datasource AUTOKOM introuvable après provisionnement')
    out('✓', f'Grafana API : source Prometheus vérifiée ({cfg().prometheus_base})')


def ensure_grafana(args):
    prev = previous_state()
    if (prev.get('grafana_datasource_uid') and
            prev['grafana_datasource_uid'] != cfg().datasource_uid):
        raise SetupError('Changer l’UID d’une source Grafana existante créerait un doublon : migration manuelle requise')
    exists = package_installed('grafana')
    if first_install(args.mode, exists):
        autokom_banner('grafana', initial_install=True)
    out('›', f'Grafana OSS : {"déjà installé" if exists else "absent"}')
    if args.mode == 'check':
        out('+', f'Configurer Grafana sur {cfg().grafana_base}, source Prometheus déclarative')
        return
    if GRAFANA_DROPIN.exists() and 'AUTOKOM managed' not in GRAFANA_DROPIN.read_text():
        raise SetupError('Override Grafana existant non géré : refus')
    if exists and active('grafana-server'):
        override_changed = not GRAFANA_DROPIN.exists() or GRAFANA_DROPIN.read_text() != unit_override_grafana(args.grafana_domain)
        ds_changed = not GRAFANA_DS.exists() or GRAFANA_DS.read_text() != datasource_yaml()
        if (override_changed or ds_changed) and not yesno('Grafana existant : confirmer mise à jour, source et redémarrage ?', False, noninteractive=args.non_interactive):
            raise SetupError('Grafana existant conservé : modification non autorisée')
    changed_override = owned_write(GRAFANA_DROPIN, unit_override_grafana(args.grafana_domain))
    run(['systemctl', 'daemon-reload'])
    fresh = not exists or (Path('/var/lib/grafana/grafana.db').exists() and
                           grafana_api('admin', 'admin', '/api/user')[0] == 200)
    if not exists:
        run(['apt-get', 'install', '-y', 'ca-certificates', 'gnupg', 'wget', 'apt-transport-https'], timeout=240)
        key = Path('/etc/apt/keyrings/grafana.asc')
        key.parent.mkdir(parents=True, exist_ok=True)
        if not key.exists():
            # Download via HTTPS and verify content is a public OpenPGP key.
            with Activity('Téléchargement de la clé officielle Grafana', delay=0.4):
                with urllib.request.urlopen('https://apt.grafana.com/gpg-full.key', timeout=30) as p:
                    key_data = p.read(120000)
            if b'BEGIN PGP PUBLIC KEY BLOCK' not in key_data:
                raise SetupError('Clé Grafana téléchargée invalide')
            owned_write(key, key_data.decode('ascii'))
        owned_write(Path('/etc/apt/sources.list.d/autokom-grafana.list'),
                    'deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main\n')
        run(['apt-get', 'update', '-qq'], timeout=240)
        run(['apt-get', 'install', '-y', '--no-install-recommends', 'grafana'], timeout=420)
    # Do not modify any non-AUTOKOM managed provisioning file.
    changed = owned_write(GRAFANA_DS, datasource_yaml())
    if changed or changed_override or not active('grafana-server') or not exists:
        run(['systemctl', 'restart', 'grafana-server'], timeout=140)
    else:
        run(['systemctl', 'start', 'grafana-server'])
    with Activity('Attente du démarrage de Grafana', delay=0.4):
        for _ in range(30):
            if get_local(cfg().grafana_base + '/api/health')[0] == 200:
                break
            time.sleep(1)
        else:
            raise SetupError(f'Grafana ne répond pas sur {cfg().grafana_base}')
    assert_private(cfg().grafana_port)
    auth = grafana_credentials(fresh, args)
    if auth:
        verify_grafana_datasource(*auth)
    else:
        out('!', 'Grafana existant : source présente sur disque ; API non vérifiée sans identifiants')
    out('✓', 'Grafana opérationnel ; provisionnement de la source Prometheus contrôlé')



def server_ranges(text):
    """Return (start,end) of balanced nginx `server {}` blocks; skip comments.

    Conservative structural scanner: only used for an unambiguous Jenkins vhost.
    """
    stripped = re.sub(r'(?m)#.*$', lambda m: ' ' * len(m.group()), text)
    for match in re.finditer(r'(?<![\w-])server\s*\{', stripped):
        start = match.start()
        depth = 1
        i = match.end()
        while i < len(stripped) and depth:
            if stripped[i] == '{':
                depth += 1
            elif stripped[i] == '}':
                depth -= 1
            i += 1
        if depth == 0:
            yield start, i


def jenkins_vhost_patch(text, domain):
    metric_path = re.escape(cfg().jenkins_metrics_path)
    """Add a 403 for Jenkins metrics in one existing HTTPS server.

    Reject ambiguous/conflicting location /prometheus/ configurations.
    """
    matches = []
    for start, end in server_ranges(text):
        block = text[start:end]
        domain_names = re.findall(r'(?m)^\s*server_name\s+([^;]+);', block)
        if not any(domain in names.lower().split() for names in domain_names):
            continue
        if not re.search(r'(?m)^\s*listen\s+(?:\[::\]:)?443\b[^;]*\bssl\b', block):
            continue
        if re.search(r'location\s+\^~\s+' + metric_path + r'\s*\{', block):
            if re.search(r'(?s)location\s+\^~\s+' + metric_path + r'\s*\{[^}]*\b(?:deny\s+all|return\s+403)\s*;', block):
                return text, False
            raise SetupError('Un bloc /prometheus/ existe sans refus explicite; ne pas le remplacer')
        match = re.search(r'(?m)^\s*location\s+/\s*\{', block)
        if match is None:
            raise SetupError('Vhost Jenkins TLS sans location / identifiable : durcissement automatique refusé')
        matches.append(start + match.start())
    if len(matches) != 1:
        raise SetupError(f'Vhost Jenkins HTTPS ambigu pour {domain} : {len(matches)} blocs')
    addition = ('    # AUTOKOM: les métriques Jenkins sont collectées par loopback uniquement\n'
                f'    location ^~ {cfg().jenkins_metrics_path} {{ return 403; }}\n\n')
    at = matches[0]
    return text[:at] + addition + text[at:], True



def ensure_private_jenkins(args):
    """Protect direct Jenkins listener; avoids bypassing Nginx metrics ACL."""
    if not active('jenkins'):
        raise SetupError('Jenkins doit être actif avant la phase Observabilité')
    ports = listener(cfg().jenkins_port)
    if not ports:
        if args.mode == 'check':
            out('!', f'Jenkins actif mais TCP/{cfg().jenkins_port} introuvable ; configurer le port réel avant l’application')
            return
        raise SetupError(f'Jenkins déjà actif, mais aucun écouteur TCP/{cfg().jenkins_port}. '
                         'Configurer son port via le module Jenkins puis relancer. Pas de migration implicite.')
    if private_listener(ports):
        out('✓', 'Jenkins direct écoute uniquement sur loopback')
        return
    if args.mode == 'check':
        out('!', f'Jenkins {cfg().jenkins_port} doit être restreint à 127.0.0.1 (actuel : {ports})')
        return
    if not yesno(f'Jenkins {cfg().jenkins_port} public : limiter à 127.0.0.1 avec redémarrage du service ?',
                 False, noninteractive=args.non_interactive):
        raise SetupError('Jenkins écoute publiquement sur son port configuré; correction requise avant la collecte')
    path = Path('/etc/systemd/system/jenkins.service.d/90-autokom-local.conf')
    if path.is_file() and 'AUTOKOM managed' not in path.read_text():
        raise SetupError('Override Jenkins inconnu : pas de remplacement automatique')
    owned_write(path, '# AUTOKOM managed: Jenkins private behind Nginx\n'
                      f'[Service]\nEnvironment="JENKINS_LISTEN_ADDRESS={cfg().jenkins_host}"\n'
                      f'Environment="JENKINS_PORT={cfg().jenkins_port}"\n')
    run(['systemctl', 'daemon-reload'])
    out('!', 'Redémarrage Jenkins explicitement autorisé (jobs actifs à vérifier)')
    run(['systemctl', 'restart', 'jenkins'], timeout=180)
    with Activity('Vérification de l’écoute Jenkins après redémarrage', delay=0.4):
        for _ in range(30):
            current = listener(cfg().jenkins_port)
            if private_listener(current):
                break
            time.sleep(2)
    assert_private(cfg().jenkins_port)


def harden_public_jenkins(args):
    """Deny unauthenticated Jenkins metrics at Nginx before enabling exporter.

    The Prometheus scraper uses the configured localhost Jenkins endpoint, so Nginx denial
    never breaks localhost collection.
    """
    try:
        from nginx_discovery import inventory
        domains = sorted(x.name for x in inventory().values() if x.proxy_ok)
    except ImportError as e:
        raise SetupError('Module nginx_discovery.py nécessaire pour sécuriser Jenkins') from e
    if not domains:
        out('›', 'Aucun domaine Jenkins HTTPS actif détecté ; vérifier tout autre proxy en amont')
        return
    candidates = []
    for domain in domains:
        matches = []
        for entry in NGINX_ENABLED.iterdir():
            if not entry.is_file():
                continue
            try:
                path = entry.resolve(strict=True)
                if not (str(path).startswith('/etc/nginx/sites-available/') or
                        str(path).startswith('/etc/nginx/sites-enabled/')):
                    continue
                text = path.read_text(encoding='utf-8')
                patched, changed = jenkins_vhost_patch(text, domain)
            except (PermissionError, OSError, UnicodeDecodeError, SetupError):
                continue
            matches.append((path, text, patched, changed, domain))
        if len(matches) != 1:
            raise SetupError(f'Impossible de trouver sans ambiguïté le vhost HTTPS Jenkins {domain} : ne pas publier les métriques')
        candidates.extend(matches)
    # Several domains might share one file; group patches before writing.
    changes = {}
    for path, old, patched, changed, domain in candidates:
        source = changes.get(path, old)
        changed_content, modified = jenkins_vhost_patch(source, domain)
        changes[path] = changed_content
    to_write = [(path, content) for path, content in changes.items() if path.read_text() != content]
    if not to_write:
        out('✓', 'Accès /prometheus/ bloqué sur les domaines HTTPS Jenkins existants')
        return
    if args.mode == 'check':
        out('+', 'Restreindre /prometheus/ à Jenkins interne (interdire accès via Nginx public)')
        return
    if not yesno('Sécuriser les virtual hosts Jenkins (bloquer /prometheus/ public) ?', True,
                 noninteractive=args.non_interactive):
        raise SetupError('Protection Jenkins metrics requise avant installation de Prometheus')
    backup = {path: path.read_text(encoding='utf-8') for path, _ in to_write}
    try:
        for path, content in to_write:
            owned_write(path, content)
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
    except Exception:
        for path, content in backup.items():
            owned_write(path, content)
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        raise
    out('✓', 'Routes Jenkins /prometheus/ protégées : accès public 403, scraper localhost maintenu')

def ensure_jenkins_exporter(args):
    if not active('jenkins'):
        raise SetupError('Jenkins doit être actif avant Prometheus/Grafana')
    with Activity('Contrôle de l’exporter Jenkins', delay=0.4):
        code, sample = get_local(cfg().jenkins_metrics_url)
    if code == 200 and ('# HELP ' in sample or '# TYPE ' in sample):
        out('✓', 'Jenkins exporte déjà /prometheus/')
        return None
    present = Path('/var/lib/jenkins/plugins/prometheus.jpi').exists() or Path('/var/lib/jenkins/plugins/prometheus.hpi').exists()
    if not present:
        if args.mode == 'check':
            out('+', 'Installer le plugin Jenkins prometheus et vérifier /prometheus/')
            return None
        if args.non_interactive:
            raise SetupError('Plugin Jenkins manquant : relancer le post-install Jenkins avec authentification administrateur')
        if not yesno('Installer maintenant le plugin Jenkins Prometheus ?', True):
            raise SetupError('L’export Jenkins est requis pour continuer')
        from jenkins_postinstall import (authorization_context, ensure_cli_jar, cli,
                                         jenkins_state, trusted_web_url)
        if jenkins_state() != 'secure':
            raise SetupError('Jenkins non initialisé ou non sécurisé : terminer le post-install Jenkins d’abord')
        from types import SimpleNamespace
        authargs = SimpleNamespace(auth_file=None, interactive=True)
        with authorization_context(authargs, trusted_web_url(), apply=True) as auth_file:
            ensure_cli_jar(True)
            cli(auth_file, ['install-plugin', 'prometheus', '-deploy'], timeout=420,
                status='Installation du plugin Prometheus')
            # Confirm before safe restart to avoid disrupting active CI jobs.
            if yesno('Redémarrage sécurisé Jenkins pour activer le plugin ?', True):
                cli(auth_file, ['safe-restart'], timeout=50, status='Redémarrage sécurisé Jenkins')
            else:
                raise SetupError('Plugin installé mais redémarrage non autorisé : relancer après redémarrage Jenkins')
        with Activity('Attente du redémarrage de l’exporter Jenkins', delay=0.4):
            for _ in range(60):
                if get_local(cfg().jenkins_metrics_url)[0] in (200, 401, 403):
                    break
                time.sleep(2)
            code, sample = get_local(cfg().jenkins_metrics_url)
    if code == 200 and ('# HELP ' in sample or '# TYPE ' in sample):
        out('✓', 'Jenkins exporter opérationnel')
        return None
    if code in (401, 403):
        out('!', 'Endpoint Jenkins protégé : compte lecture Metrics.VIEW requis pour Prometheus')
        if args.mode == 'check':
            return None
        username = prompt_text('Utilisateur Jenkins lecture métriques', noninteractive=args.non_interactive)
        password = secret('Token Jenkins lecture métriques (pas le token admin)', args.non_interactive)
        code2, body = get_local(cfg().jenkins_metrics_url, username=username, password=password)
        if code2 != 200 or ('# HELP ' not in body and '# TYPE ' not in body):
            raise SetupError('Compte lecture métriques invalide ; aucune autorisation Jenkins n’a été contournée')
        # Keep credential in memory only until Prometheus is installed and its
        # dedicated OS group exists; then store root:prometheus 0640.
        return (username, password)
    if args.mode == 'check':
        out('!', f'Endpoint Jenkins métriques non prêt : HTTP {code} (à vérifier après installation plugin)')
        return None
    raise SetupError(f'Jenkins /prometheus/ non utilisable : HTTP {code}; consulter Jenkins et le plugin')


def check_only(args):
    section('Prévisualisation', 'Jenkins → Prometheus → Grafana · aucune modification')
    out('›', 'Simulation en lecture seule : aucune installation, aucun certificat, aucun utilisateur créé')
    if not active('jenkins'):
        out('!', 'Jenkins inactif — il faudra l’activer avant la phase 3')
    else:
        ensure_private_jenkins(args)
        harden_public_jenkins(args)
        ensure_jenkins_exporter(args)
    out('›', f'Prometheus : {"présent" if package_installed("prometheus") else "à installer"}')
    out('›', f'Grafana : {"présent" if package_installed("grafana") else "à installer"}')
    for port in (cfg().prometheus_port, cfg().grafana_port):
        li = listener(port)
        if li:
            out('›', f'Écoute TCP/{port} détectée : {", ".join(li)}')
    for kind, domain, port in (('prometheus', args.prometheus_domain, cfg().prometheus_port),
                               ('grafana', args.grafana_domain, cfg().grafana_port)):
        if domain:
            existing = existing_vhosts(domain)
            if existing:
                out('✓' if vhost_target_ok(existing, port) and valid_cert(domain) else '!',
                    f'{kind} : domaine {domain} ' + ('existant et conforme' if vhost_target_ok(existing, port) and valid_cert(domain) else 'en conflit ou certificat à vérifier'))
            else:
                out('+', f'{kind} : créer Nginx/Certbot pour {domain}')
        else:
            out('✓', f'{kind} : local uniquement')
    out('✓', 'Simulation terminée : lancer --apply seulement après vérification')


def prompt_domain(label, *, max_attempts=3):
    """Un choix « Oui, domaine » ne doit jamais accepter un champ vide."""
    for number in range(1, max_attempts + 1):
        response = prompt_text(label, hint='Nom DNS complet, ex. metrics.exemple.com (sans https://)')
        try:
            domain = validate_domain(response)
            if not domain:
                raise SetupError('Domaine obligatoire après sélection « Oui »')
            return domain
        except SetupError as exc:
            remaining = max_attempts - number
            if not remaining:
                raise
            out('!', f'{exc} · {remaining} essai(s) restant(s)')
    raise SetupError('Domaine invalide')


def interactive_options(args):
    if args.non_interactive:
        args.prometheus_domain = validate_domain(args.prometheus_domain)
        args.grafana_domain = validate_domain(args.grafana_domain)
        return
    if not args.prometheus_domain:
        if yesno('Prometheus dispose-t-il d’un domaine HTTPS ?', False):
            args.prometheus_domain = prompt_domain('Domaine Prometheus')
    if not args.grafana_domain:
        if yesno('Grafana dispose-t-il d’un domaine HTTPS ?', False):
            args.grafana_domain = prompt_domain('Domaine Grafana')
    if args.prometheus_domain == args.grafana_domain and args.prometheus_domain:
        raise SetupError('Prometheus et Grafana ne peuvent pas utiliser le même domaine')
    if (args.prometheus_domain or args.grafana_domain) and not args.email:
        # cert issuance requires email only for new domains; existing domains don't.
        if any(not existing_vhosts(d) for d in [args.prometheus_domain, args.grafana_domain] if d):
            args.email = prompt_text('E-mail Let’s Encrypt', hint='Pour certificats nouveaux et alertes de renouvellement')


def preflight(args):
    if os.geteuid() != 0:
        raise SetupError('AUTOKOM nécessite sudo/root pour la gestion des services')
    if not Path('/etc/os-release').is_file() or 'ID=ubuntu' not in Path('/etc/os-release').read_text():
        raise SetupError('Module prévu actuellement pour Ubuntu')
    for exe in ('systemctl', 'ss', 'nginx'):
        if not shutil.which(exe):
            raise SetupError(f'Commande nécessaire manquante : {exe}')
    if args.prometheus_domain or args.grafana_domain:
        if not shutil.which('certbot'):
            raise SetupError('Certbot doit être installé par AUTOKOM Foundation')
    if args.prometheus_domain and not shutil.which('openssl'):
        raise SetupError('OpenSSL requis pour protéger Prometheus par mot de passe')
    # Refuse unhandled local port conflicts or public exposures.
    for port, expected in ((cfg().prometheus_port, 'prometheus'),
                           (cfg().grafana_port, 'grafana-server')):
        if listener(port) and not active(expected):
            raise SetupError(f'TCP/{port} est déjà occupé par un service inconnu : ne pas écraser')


def preflight_domains(args):
    """Contrôles réseau avant tout redémarrage/installation/Certbot.

    Sur EC2 direct, DNS A doit pointer vers l'IP publique IMDSv2.
    Pour les déploiements ALB/CDN, préférer un profil d'infrastructure dédié.
    """
    requested = [x for x in (args.prometheus_domain, args.grafana_domain) if x]
    if not requested:
        return
    out('›', 'Vérification DNS des domaines et de l’IP publique AWS…')
    for domain in requested:
        with Activity(f'DNS / IP publique EC2 · {domain}', delay=0.4):
            try:
                ips = check_direct_ec2_domain(domain, expected_ipv4=args.public_ip)
            except DomainRoutingError as exc:
                raise SetupError(str(exc)) from exc
        out('✓', f'{domain} → {", ".join(ips)} (DNS conforme à l’instance)')
    with Activity('Recherche des domaines Nginx existants', delay=0.4):
        needs_certificate = any(not existing_vhosts(domain) for domain in requested)
    if needs_certificate:
        with Activity('Contrôle local du port 80 pour HTTP-01', delay=0.4):
            try:
                check_http01_local()
            except DomainRoutingError as exc:
                raise SetupError(str(exc)) from exc
        out('✓', 'Nginx/TCP 80 accessible localement')
        out('!', 'HTTP-01 doit aussi être joignable depuis Internet (AWS SG/UFW) ; Certbot le vérifiera')


def interactive_prometheus_password(args):
    """Les secrets sont demandés seulement APRÈS validation des domaines."""
    if (args.prometheus_domain and args.mode == 'apply' and
            not PROM_HTPASSWD.exists()):
        args.prometheus_user = prompt_text('Utilisateur Prometheus HTTPS',
            default='metricsadmin', hint='Authentification Nginx ; identifiants locaux')
        args.prometheus_password = password_twice('Prometheus HTTPS', args.non_interactive)


def main(argv=None):
    parser = argparse.ArgumentParser(description='AUTOKOM Phase 3 — Jenkins/Prometheus/Grafana')
    mode_flags = parser.add_mutually_exclusive_group()
    mode_flags.add_argument('--check', dest='mode', action='store_const', const='check')
    mode_flags.add_argument('--apply', dest='mode', action='store_const', const='apply')
    parser.set_defaults(mode='check')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--prometheus-domain', default=None)
    parser.add_argument('--grafana-domain', default=None)
    parser.add_argument('--email', default=None)
    parser.add_argument('--grafana-user', default='')
    parser.add_argument('--public-ip', default=None,
                        help='IP publique IPv4 directe, seulement si IMDSv2 indisponible (jamais une IP privée)')
    parser.add_argument('--config', default=os.environ.get('AUTOKOM_OBSERVABILITY_CONFIG'),
                        help='Fichier INI privé de surcharges non sensibles')
    parser.add_argument('--set', action='append', default=[], metavar='SECTION.KEY=VALUE',
                        help='Surcharge validée pour une seule exécution (répétable)')
    parser.add_argument('--show-config', action='store_true',
                        help='Afficher les paramètres et URLs déduits sans sudo ni modification')
    args = parser.parse_args(argv)
    global SETTINGS
    SETTINGS = load_settings(custom_path=args.config, changes=args.set)
    SETTINGS = validate_settings(replace(SETTINGS,
        prometheus_domain=SETTINGS.prometheus_domain if args.prometheus_domain is None else args.prometheus_domain,
        grafana_domain=SETTINGS.grafana_domain if args.grafana_domain is None else args.grafana_domain,
        email=SETTINGS.email if args.email is None else args.email))
    if args.show_config:
        print('AUTOKOM · Configuration d’observabilité (aucun secret)')
        for key, value in SETTINGS.display().items():
            print(f'  {key:<33} {value}')
        return 0
    args.prometheus_domain = SETTINGS.prometheus_domain
    args.grafana_domain = SETTINGS.grafana_domain
    args.email = SETTINGS.email
    args.prometheus_password = ''
    args.prometheus_user = ''
    args.prometheus_domain = validate_domain(args.prometheus_domain)
    args.grafana_domain = validate_domain(args.grafana_domain)
    if args.prometheus_domain and args.prometheus_domain == args.grafana_domain:
        raise SetupError('Les domaines Prometheus et Grafana doivent être différents')
    if not args.non_interactive and not os.isatty(0):
        # prompts read /dev/tty, not stdin; caller may pipe installer from URL.
        try:
            fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY); os.close(fd)
        except OSError:
            args.non_interactive = True
    section('06 / 06 · Observabilité', 'Jenkins → Prometheus → Grafana')
    interactive_options(args)
    SETTINGS = validate_settings(replace(SETTINGS, prometheus_domain=args.prometheus_domain,
                                          grafana_domain=args.grafana_domain, email=args.email))
    out('›', f'Ports actifs ciblés : Jenkins {cfg().jenkins_port} · Prometheus {cfg().prometheus_port} · Grafana {cfg().grafana_port}')
    preflight(args)
    preflight_domains(args)
    if args.mode == 'check':
        check_only(args)
        return 0
    interactive_prometheus_password(args)
    ensure_private_jenkins(args)
    harden_public_jenkins(args)
    auth = ensure_jenkins_exporter(args)
    if args.prometheus_domain and not PROM_HTPASSWD.exists():
        if not args.prometheus_password:
            raise SetupError('Compte Prometheus manquant : exécuter en mode interactif')
        ensure_prom_basic_auth(args.prometheus_user, args.prometheus_password)
        args.prometheus_password = ''
    ensure_prometheus(args, auth_user=auth[0] if auth else None, auth_password=auth[1] if auth else None)
    ensure_grafana(args)
    configure_nginx(args.prometheus_domain, cfg().prometheus_port, 'prometheus', args)
    configure_nginx(args.grafana_domain, cfg().grafana_port, 'grafana', args)
    # Final functional check: Prometheus must actually scrape the Jenkins job.
    seen = False
    with Activity('Collecte Jenkins → Prometheus (cible UP)', delay=0.4):
        for attempt in range(4):
            status, payload = get_local(cfg().prometheus_base + '/api/v1/targets')
            if status == 200:
                try:
                    targets = json.loads(payload).get('data', {}).get('activeTargets', [])
                    seen = any(t.get('health') == 'up' and
                               (t.get('labels', {}).get('job') == cfg().jenkins_job or
                                t.get('discoveredLabels', {}).get('job') == cfg().jenkins_job)
                               for t in targets)
                except (ValueError, TypeError, AttributeError):
                    seen = False
                if seen:
                    break
            if attempt < 3:
                time.sleep(10)
    if not seen:
        raise SetupError('Scrape Jenkins non confirmé UP dans Prometheus : vérifier /targets, credentials et le plugin')
    else:
        out('✓', 'Prometheus collecte les métriques Jenkins (cible UP)')
    save_state()
    out('✓', 'AUTOKOM observabilité configurée : exporter, Prometheus, Grafana, Nginx et TLS vérifiés')
    if not args.grafana_domain:
        out('›', f'Accès Grafana privé : ssh -L {cfg().grafana_port}:127.0.0.1:{cfg().grafana_port} ubuntu@SERVEUR')
    if not args.prometheus_domain:
        out('›', f'Accès Prometheus privé : ssh -L {cfg().prometheus_port}:127.0.0.1:{cfg().prometheus_port} ubuntu@SERVEUR')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (SetupError, ConfigError, ValueError, OSError, subprocess.TimeoutExpired) as err:
        out('✗', str(err))
        sys.exit(1)
    except KeyboardInterrupt:
        out('!', 'Annulé par utilisateur')
        sys.exit(130)
