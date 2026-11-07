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
import http.client
import secrets
import stat
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
from domain_validation import DomainRoutingError, check_direct_instance_domain as check_direct_ec2_domain, check_http01_local

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
ACME_WEBROOT = Path('/var/www/autokom-acme')
CONFIG_STATE = Path('/etc/autokom/state/observability.json')
CONFIG_PENDING = Path('/var/lib/autokom/install/observability-selection.json')
SMTP_PENDING = Path('/var/lib/autokom/install/observability-smtp-private.json')
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



def previous_selection():
    """Only non-secret, root-owned wizard choices; no passwords or API tokens."""
    path = CONFIG_PENDING
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise SetupError('Choix observabilité non fiable (lien symbolique ou fichier non régulier)')
    st = path.stat()
    if st.st_uid != 0 or st.st_mode & 0o077:
        raise SetupError('Choix observabilité non privés : propriétaire root et mode 0600 requis')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('schema') != 1:
            raise ValueError('schema')
        allowed = ('prometheus_domain', 'grafana_domain', 'email', 'grafana_user')
        flags = ('email_decided', 'extras_decided', 'setup_node', 'setup_cadvisor',
                 'setup_alertmanager', 'smtp_enabled')
        if any(not isinstance(record.get(key, ''), str) for key in allowed) or any(
                key in record and type(record[key]) is not bool for key in flags):
            raise ValueError('format')
        validate_domain(record.get('prometheus_domain', ''))
        validate_domain(record.get('grafana_domain', ''))
        return record
    except (ValueError, OSError) as exc:
        raise SetupError('Choix observabilité illisibles : utiliser sudo autokom configure observability') from exc


def save_selection(args):
    if args.mode != 'apply':
        return
    from autokom_install_permissions import private_install_directory
    try:
        private_install_directory(CONFIG_PENDING.parent)
    except RuntimeError as exc:
        raise SetupError(str(exc)) from exc
    record = {
        'schema': 1,
        'prometheus_domain': args.prometheus_domain or '',
        'grafana_domain': args.grafana_domain or '',
        'email': args.email or '',
        'grafana_user': args.grafana_user or '',
        'email_decided': bool(getattr(args, '_cert_email_decided', False)),
        'extras_decided': bool(getattr(args, '_extras_decided', False)),
        'setup_node': bool(getattr(args, 'setup_node', False)),
        'setup_cadvisor': bool(getattr(args, 'setup_cadvisor', False)),
        'setup_alertmanager': bool(getattr(args, 'setup_alertmanager', False)),
        'smtp_enabled': bool(getattr(args, '_smtp_enabled', False)),
    }
    owned_write(CONFIG_PENDING, json.dumps(record, sort_keys=True, indent=2) + '\n', mode=0o600)
    out('✓', 'Choix Prometheus/Grafana enregistrés (sans mot de passe) pour la reprise')


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
        'prometheus_domain': settings.prometheus_domain,
        'grafana_domain': settings.grafana_domain,
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


def apt_package_available(name):
    """Return whether APT exposes a standalone package (not a command name).

    Ubuntu 24.04 ships /usr/bin/promtool in `prometheus`; newer releases
    may split `promtool` into a separate package. Consult the local signed
    APT index after updating it, never guess from a distro version string.
    """
    try:
        proc = subprocess.run(['apt-cache', 'show', '--no-all-versions', name],
                              capture_output=True, text=True, timeout=20,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f'Impossible de consulter le catalogue APT pour {name}') from exc
    return proc.returncode == 0 and bool(proc.stdout.strip())


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



def wait_service_ready(unit, port, url, *, attempts=35, interval=2):
    """Attendre une écoute privée ET un endpoint HTTP prêt, sans course systemd.

    `systemctl restart` peut réussir alors que le processus (Type=simple) n'a
    pas encore ouvert le socket. L'absence transitoire de port n'est donc pas
    une erreur, contrairement à un port effectivement exposé à Internet.
    """
    with Activity(f'{unit} · démarrage et contrôle de santé', delay=0.4):
        for n in range(attempts):
            addresses = listener(port)
            if addresses:
                if not private_listener(addresses):
                    raise SetupError(f'{unit} : TCP/{port} accessible hors loopback '
                                     f'({", ".join(addresses)}). Arrêt par sécurité.')
                status, _ = get_local(url, timeout=3)
                if status == 200:
                    return addresses
            if n < attempts - 1:
                time.sleep(interval)
    # A failure may be a crash-loop, invalid flags, or permissions on TSDB;
    # explain safe diagnostic commands without dumping logs (possibly secrets).
    try:
        state = subprocess.run(['systemctl', 'is-active', unit], capture_output=True,
                               text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        state = 'indéterminé'
    raise SetupError(f'{unit} non prêt sur 127.0.0.1:{port} après '
                     f'{attempts * interval}s (systemd : {state}). '
                     f'Diagnostic : systemctl status {unit} --no-pager ; '
                     f'journalctl -u {unit} -n 50 --no-pager')

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
    # Original direct proxy and managed named upstreams are both supported.
    return any(f'127.0.0.1:{port}' in v or f'localhost:{port}' in v or
               f'proxy_pass http://autokom_prometheus_{port}' in v or
               f'proxy_pass http://autokom_grafana_{port}' in v
               for v in vhosts)


def valid_cert(domain):
    fullchain = Path('/etc/letsencrypt/live') / domain / 'fullchain.pem'
    if not fullchain.is_file():
        return False
    return (subprocess.run(['openssl', 'x509', '-in', str(fullchain),
                            '-noout', '-checkend', '604800'], capture_output=True).returncode == 0 and
            subprocess.run(['openssl', 'x509', '-in', str(fullchain),
                            '-noout', '-checkhost', domain], capture_output=True).returncode == 0)


def _acme_location():
    # Use a dedicated, readable webroot. `^~` takes priority over the generic
    # HTTP 503 gate, 301 redirect, and other regex locations (e.g. dotfiles).
    return ('    location ^~ /.well-known/acme-challenge/ {\n'
            f'        root {ACME_WEBROOT};\n'
            '        default_type text/plain;\n'
            '        try_files $uri =404;\n'
            '        access_log off;\n'
            '    }\n')


def _old_gated_vhost(domain, port):
    # Exact AUTOKOM HTTP 503 placeholder deployed before webroot support.
    return (f'# AUTOKOM MANAGED {domain} : {port} — TLS PENDING\n'
            'server {\n    listen 80;\n'
            f'    server_name {domain};\n'
            '    location / { return 503; }\n}\n')


def ensure_acme_webroot():
    """Create only AUTOKOM's isolated ACME directory, never follow symlinks.

    The challenge must be readable by www-data, but not writable by it or by
    other accounts. Existing foreign directories are never reowned/chmodded.
    """
    root = ACME_WEBROOT
    if not root.is_absolute():
        raise SetupError('Répertoire ACME non absolu')
    # Nginx can return 404 (rather than 403) when it cannot traverse an
    # ancestor directory. Validate the *whole* path, not only /var/www.
    # Do not chmod/chown existing parent directories; those are not owned by
    # this module and may have intentional policy restrictions.
    for parent in reversed(root.parents):
        if parent.is_symlink():
            raise SetupError(f'Parent ACME symbolique interdit : {parent}')
        try:
            mode = parent.stat().st_mode
        except OSError as exc:
            raise SetupError(f'Parent ACME inaccessible : {parent}') from exc
        if not stat.S_ISDIR(mode) or not (stat.S_IMODE(mode) & 0o001):
            raise SetupError(f'Parent ACME non traversable par Nginx : {parent} ; '
                             'ne pas changer ses droits sans validation')
    parents = [root.parent, root, root / '.well-known',
               root / '.well-known' / 'acme-challenge']
    for path in parents:
        if path.is_symlink():
            raise SetupError(f'Chemin ACME symbolique interdit : {path}')
        if not path.exists():
            path.mkdir(mode=0o755)
        st = path.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) & 0o022 or
                stat.S_IMODE(st.st_mode) & 0o005 != 0o005):
            raise SetupError(f'Répertoire ACME non fiable ou illisible par Nginx : {path}')
    return root


def _acme_ingress_local_ipv4():
    """Source IPv4 of the default outbound route, usually GCP ens4 / AWS eth0.

    Google/AWS NAT delivers public TCP/80 to this private NIC, *not* to lo.
    `ip route get` is purely a local routing-table lookup; no network request.
    Fail closed to localhost when unavailable, rather than guessing a Docker IP.
    """
    try:
        result = subprocess.run(['ip', '-j', '-4', 'route', 'get', '1.1.1.1'],
                                capture_output=True, text=True, timeout=3)
        if result.returncode:
            return None
        routes = json.loads(result.stdout)
        if not isinstance(routes, list) or not routes:
            return None
        route = routes[0]
        device = route.get('dev', '')
        if not isinstance(device, str) or (device.startswith(('docker', 'br-', 'veth', 'wg', 'tun'))):
            return None
        ip = route.get('prefsrc') or route.get('src')
        parsed = ipaddress.ip_address(ip)
        if parsed.version != 4 or parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            return None
        return str(parsed)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None


def _acme_probe(ip, domain, uri, marker):
    """Check a real Host-routed token on an exact local listening address."""
    connection = http.client.HTTPConnection(ip, 80, timeout=6)
    try:
        connection.request('GET', uri, headers={'Host': domain})
        response = connection.getresponse()
        body = response.read(256).decode('utf-8', 'replace')
        return response.status, body == marker
    finally:
        connection.close()


def check_acme_http01_local(domain):
    """Probe the exact ACME file via the NIC receiving inbound GCP/AWS NAT.

    The older preflight checked only 127.0.0.1. On Nginx hosts with an
    address-specific `listen 127.0.0.1:80`, that can select a *different*
    virtual-server group from the one servicing requests arriving on ens4.
    A real Host header, a unique token and the routed NIC are both necessary.
    """
    root = ACME_WEBROOT / '.well-known' / 'acme-challenge'
    name = 'autokom-check-' + secrets.token_hex(12)
    marker = 'autokom-acme-ready-' + secrets.token_hex(14)
    dest = root / name
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            os.fchmod(handle.fileno(), 0o644)
            handle.write(marker)
        path = '/.well-known/acme-challenge/' + name
        routed = _acme_ingress_local_ipv4()
        # Require the routed NIC when known: on GCP the public address is NATed
        # to this private NIC. Loopback is merely an additional diagnostic.
        addresses = [routed, '127.0.0.1'] if routed else ['127.0.0.1']
        results = []
        for address in dict.fromkeys(ip for ip in addresses if ip):
            try:
                status, content_ok = _acme_probe(address, domain, path, marker)
                results.append((address, str(status), content_ok))
            except (OSError, http.client.HTTPException):
                results.append((address, 'connexion impossible', False))
        decisive = results[0]
        if decisive[1] != '200' or not decisive[2]:
            report = '; '.join(f'{ip}: HTTP {code}' + (' (contenu incorrect)' if code == '200' and not valid else '')
                               for ip, code, valid in results)
            reason = ('Nginx ne sert pas le fichier ACME sur l’interface d’entrée. '
                      'Vérifier les listen 80/127.0.0.1, les server_name actifs '
                      '(sudo nginx -T), puis le webroot et les permissions de lecture '
                      'dans /var/www/autokom-acme ; examiner le journal d’erreur Nginx. '
                      'Aucune demande Let’s Encrypt envoyée.')
            raise SetupError(f'Préflight HTTP-01 {domain} : {report}. {reason}')
        out('✓', f'{domain} : défi HTTP-01 vérifié sur {decisive[0]}:80 (HTTP 200)')
        if routed and len(results) > 1 and results[1][1] != '200':
            out('›', 'Loopback Nginx différent de l’interface publique ; validation sur l’interface routée confirmée')
    finally:
        dest.unlink(missing_ok=True)


def vhost_content(domain, port, *, prometheus=False, gated=True, hardened_proxy=True):
    """Start HTTP gated, then publish HTTPS only after certonly succeeds."""
    if gated:
        return (f'# AUTOKOM MANAGED {domain} : {port} — TLS PENDING\n'
                'server {\n    listen 80;\n'
                f'    server_name {domain};\n'
                + _acme_location() +
                '    location / { return 503; }\n}\n')
    auth = ('        auth_basic "AUTOKOM Metrics";\n'
            '        auth_basic_user_file /etc/nginx/.autokom-prometheus.htpasswd;\n') if prometheus else ''
    upstream_name = f'autokom_{"prometheus" if prometheus else "grafana"}_{port}'
    return (f'# AUTOKOM MANAGED {domain} : {port} — HTTPS\n'
            f'upstream {upstream_name} {{\n'
            f'    server 127.0.0.1:{port};\n'
            '    keepalive 8;\n'
            '}\n'
            'server {\n'
            '    listen 80;\n'
            f'    server_name {domain};\n'
            + _acme_location() +
            '    location / { return 301 https://$host$request_uri; }\n'
            '}\n'
            'server {\n'
            '    listen 443 ssl;\n'
            f'    server_name {domain};\n'
            f'    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;\n'
            f'    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;\n'
            '    ssl_protocols TLSv1.2 TLSv1.3;\n'
            '    ssl_session_tickets off;\n'
            '    location / {\n' + auth +
            f'        proxy_pass http://{upstream_name};\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header Host $host;\n'
            '        proxy_set_header X-Real-IP $remote_addr;\n'
            '        proxy_set_header X-Forwarded-Proto $scheme;\n'
            '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
            '        proxy_set_header Connection "";\n'
            + ('        proxy_set_header X-Forwarded-Host $host;\n'
               '        proxy_set_header X-Forwarded-Port $server_port;\n'
               '        proxy_connect_timeout 10s;\n'
               '        proxy_read_timeout 120s;\n'
               '        proxy_send_timeout 120s;\n' if hardened_proxy else '') +
            '    }\n'
            + (f'    location /api/live/ {{\n'
               f'        proxy_pass http://{upstream_name};\n'
               '        proxy_http_version 1.1;\n'
               '        proxy_set_header Host $host;\n'
               '        proxy_set_header X-Forwarded-Proto $scheme;\n'
               '        proxy_set_header Upgrade $http_upgrade;\n'
               '        proxy_set_header Connection "upgrade";\n'
               + ('        proxy_set_header X-Real-IP $remote_addr;\n'
                  '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
                  '        proxy_set_header X-Forwarded-Host $host;\n'
                  '        proxy_set_header X-Forwarded-Port $server_port;\n'
                  '        proxy_read_timeout 3600s;\n'
                  '        proxy_send_timeout 3600s;\n'
                  '        proxy_buffering off;\n' if hardened_proxy else '') +
               '    }\n' if not prometheus else '')
            + '}\n')


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


def issue_observability_certificate(domain, args):
    """Reuse or obtain an ACME certificate, never touch other virtual hosts.

    Recover only via the locally verified AUTOKOM ACME webroot.  Per-attempt
    diagnostics avoid reporting obsolete errors from previous attempts.
    """
    if valid_cert(domain):
        out('✓', f'{domain} : certificat TLS existant réutilisé')
        return
    if args.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.email):
        raise SetupError('Format d’e-mail Let’s Encrypt invalide')
    email_args = (['--email', args.email] if args.email else
                  ['--register-unsafely-without-email'])
    if not args.email and not getattr(args, '_cert_email_warning_emitted', False):
        out('!', 'Certbot sans e-mail : aucune notification ACME d’expiration ou de révocation')
        args._cert_email_warning_emitted = True
    from observability_dns01 import (configured_zone_credentials, ensure_certbot_credentials,
                                     ensure_dns_plugin, certbot_dns01_args, DNS01Error)
    try:
        cloudflare = configured_zone_credentials(domain)
    except DNS01Error as exc:
        raise SetupError(str(exc)) from exc
    if cloudflare is not None:
        # DNS-01 is independent of Nginx, the HTTP-01 webroot and cloud VPC
        # ingress. It is preferred when AUTOKOM already has a scoped CF token.
        # Certbot creates/removes only the ephemeral challenge TXT records.
        try:
            ensure_dns_plugin(run)
            credentials = ensure_certbot_credentials(cloudflare[1])
            challenge_args = certbot_dns01_args(domain, credentials)
        except DNS01Error as exc:
            raise SetupError(str(exc)) from exc
        out('›', f'{domain} : validation DNS-01 Cloudflare (pas de dépendance HTTP/80)')
        method = 'DNS-01 Cloudflare'
    else:
        ensure_acme_webroot()
        check_acme_http01_local(domain)
        challenge_args = ['--webroot', '-w', str(ACME_WEBROOT), '-d', domain]
        method = 'HTTP-01 Nginx'
    from observability_acme import acme_log_offset, acme_failure_detail
    log_offset = acme_log_offset()
    try:
        run(['certbot', 'certonly', '--non-interactive', '--agree-tos', '--no-eff-email',
             '--cert-name', domain, *email_args, *challenge_args], timeout=420)
    except SetupError as exc:
        detail = acme_failure_detail(domain, since=log_offset)
        raise SetupError(f'Échec HTTPS {domain} ({method}) : {detail}. '
                         'Certificat, comptes et configuration existants conservés.') from exc
    if not valid_cert(domain):
        raise SetupError(f'{domain} : Certbot terminé sans certificat valide à '
                         f'/etc/letsencrypt/live/{domain}/fullchain.pem ; '
                         'vérifier les noms des certificats avec sudo certbot certificates')


def configure_nginx(domain, port, kind, args):
    if not domain:
        out('✓', f'{kind} reste local ; aucun domaine ni virtual host créé')
        return
    existing = existing_vhosts(domain)
    managed = NGINX_AVAIL / f'autokom-{kind}-{domain}.conf'
    enabled_link = NGINX_ENABLED / managed.name
    # A previous failed HTTP-01 leaves an AUTOKOM-managed HTTP 503 gate.
    # It is *not* a foreign vhost: reuse it, never overwrite a different site.
    # A valid certificate may also exist when the previous run stopped between
    # ACME issuance and the final Nginx deployment.
    gated_content = vhost_content(domain, port, prometheus=(kind == 'prometheus'), gated=True)
    old_gated = _old_gated_vhost(domain, port)
    owned_pending = (len(existing) == 1 and managed.is_file() and
                     not managed.is_symlink() and enabled_link.is_symlink() and
                     enabled_link.resolve() == managed.resolve() and
                     managed.read_text(encoding='utf-8') in (gated_content, old_gated))
    if owned_pending:
        out('›', f'{domain} : reprise du virtual host AUTOKOM TLS PENDING (pas de réinitialisation)')
    # A previous AUTOKOM release may already have published its exact HTTPS
    # template while the certificate later expired or became invalid.  Only
    # recover an exact match: never adopt a third-party server block or rewrite
    # an edited config.  The existing HTTPS proxy remains untouched if ACME
    # fails, so a retry cannot silently replace administrator customization.
    full_content = vhost_content(domain, port, prometheus=(kind == 'prometheus'), gated=False)
    # Older AUTOKOM HTTPS sites remain owned and recoverable after a cert
    # expires. Do not automatically rewrite their validated proxy layout.
    legacy_https = vhost_content(domain, port, prometheus=(kind == 'prometheus'),
                                 gated=False, hardened_proxy=False)
    owned_https = (1 <= len(existing) <= 2 and managed.is_file() and
                   not managed.is_symlink() and enabled_link.is_symlink() and
                   enabled_link.resolve() == managed.resolve() and
                   managed.read_text(encoding='utf-8') in (full_content, legacy_https))
    if owned_https and not valid_cert(domain):
        if args.mode == 'check':
            out('+', f'{domain} : renouveler le certificat du HTTPS AUTOKOM sans remplacer le proxy')
            return
        out('›', f'{domain} : HTTPS AUTOKOM reconnu ; réparation TLS sans écraser Nginx')
        issue_observability_certificate(domain, args)
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        out('✓', f'{domain} : certificat et reverse proxy HTTPS restaurés')
        return
    if existing and not owned_pending:
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
        if kind == 'prometheus' and not any(
                ('auth_basic_user_file' in block and 'auth_basic ' in block) or
                'auth_request /internal/autokom/authz;' in block for block in existing):
            raise SetupError('Proxy Prometheus existant sans authentification HTTP : correction manuelle exigée')
        # Upgrade only our own vhost, and only with interactive approval.
        if managed.is_file() and not managed.is_symlink():
            old = managed.read_text(encoding='utf-8')
            target = vhost_content(domain, port, prometheus=(kind=='prometheus'), gated=False)
            if (old.startswith(f'# AUTOKOM MANAGED {domain} :') and
                    old != target and 'upstream autokom_' not in old):
                if yesno(f'Migrer le vhost {domain} vers un upstream Nginx nommé ?',
                         default=False, noninteractive=args.non_interactive):
                    try:
                        owned_write(managed,target)
                        run(['nginx','-t'])
                        run(['systemctl','reload','nginx'])
                    except Exception:
                        owned_write(managed,old)
                        run(['nginx','-t'])
                        run(['systemctl','reload','nginx'])
                        raise
                    out('✓', f'{domain} : upstream Nginx nommé configuré')
        out('✓', f'{domain} : virtual host et certificat existants réutilisés')
        return
    ips = resolve_domain(domain)
    out('›', f'DNS {domain} → {", ".join(ips)} ; Certbot vérifiera l’accès public HTTP-01')
    if args.mode == 'check':
        out('+', f'Créer le proxy HTTPS sécurisé : {domain} → 127.0.0.1:{port}')
        return
    if managed.exists() and not owned_pending:
        raise SetupError(f'Un fichier Nginx AUTOKOM préexiste mais n’est pas actif : {managed}')
    # Une adresse de contact est recommandée, mais Certbot peut fonctionner sans.
    # Ne jamais transmettre --email avec une chaîne vide ; utiliser le drapeau
    # explicitement prévu par Certbot et avertir l'utilisateur une seule fois.
    if args.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.email):
        raise SetupError('Format d’e-mail Let’s Encrypt invalide')
    link = NGINX_ENABLED / managed.name
    try:
        # ACME webroot is prepared only if HTTP-01 is selected. For domains
        # covered by Cloudflare, DNS-01 avoids Nginx route collisions entirely.
        # Stage gated HTTP-only site: never expose Grafana credentials on HTTP.
        # Upgrade the exact old AUTOKOM 503 placeholder in-place after failure.
        if not owned_pending:
            owned_write(managed, gated_content)
            link.symlink_to(managed)
        elif managed.read_text(encoding='utf-8') == old_gated:
            owned_write(managed, gated_content)
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        # HTTP-01 is served by the exact AUTOKOM webroot on both the
        # initial installation and all subsequent TLS recovery attempts.
        issue_observability_certificate(domain, args)

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


def ensure_prometheus_storage():
    """Prépare uniquement un nouveau dossier TSDB et refuse les liens symboliques.

    Le chemin est validé sous /var/lib/prometheus par observability_config.
    Ne touche jamais aux données/permissions d'un répertoire existant.
    """
    import pwd
    destination = Path(cfg().storage_path)
    if destination.is_symlink() or destination.parent.is_symlink():
        raise SetupError('Stockage Prometheus via lien symbolique : vérification manuelle requise')
    if destination.exists():
        if not destination.is_dir():
            raise SetupError(f'Le chemin TSDB Prometheus n’est pas un dossier : {destination}')
        return False
    try:
        user = pwd.getpwnam('prometheus')
    except KeyError as err:
        raise SetupError('Utilisateur système prometheus absent après installation du paquet') from err
    destination.mkdir(parents=True, mode=0o750, exist_ok=False)
    os.chown(destination, user.pw_uid, user.pw_gid)
    os.chmod(destination, 0o750)
    return True


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
    # `promtool` is included by the Prometheus package on Ubuntu 24.04,
    # but is an independent package on some newer Ubuntu releases.
    # Never ask apt to install a package not present in its own index.
    promtool_present = bool(shutil.which('promtool'))
    if not exists or not promtool_present:
        run(['apt-get', 'update', '-qq'], timeout=240)
        packages = ['prometheus'] if not exists else []
        if not promtool_present and apt_package_available('promtool'):
            packages.append('promtool')
        if packages:
            out('›', 'APT : installation ' + ', '.join(packages))
            run(['apt-get', 'install', '-y', '--no-install-recommends', *packages], timeout=420)
    if not shutil.which('promtool'):
        raise SetupError('Prometheus installé, mais promtool introuvable dans PATH. '
                         'Contrôler `dpkg -L prometheus` et `apt-cache policy promtool` '
                         'avant de reprendre ; aucune validation de configuration ignorée.')
    ensure_prometheus_storage()
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
    addresses = wait_service_ready('prometheus', cfg().prometheus_port,
                                   cfg().prometheus_base + '/-/ready')
    out('✓', f'Prometheus prêt · TCP/{cfg().prometheus_port} privé '
             f'({", ".join(addresses)}) · collecte Jenkins configurée')


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
        prepared = getattr(args, 'grafana_new_password', None)
        new_password = prepared if isinstance(prepared, str) and prepared else password_twice(
            'Grafana', noninteractive=args.non_interactive)
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
    # Existing accounts: options were already collected in the GRAFANA form.
    # Keep direct/expert invocations compatible with the previous prompt.
    staged = getattr(args, 'grafana_rotate', None)
    rotate = staged is True or (staged is not False and not args.non_interactive and
                                yesno('Changer les identifiants de Grafana existant ?', False))
    if rotate:
        username = getattr(args, 'grafana_rotate_user', None)
        old = getattr(args, 'grafana_old_password', None)
        new = getattr(args, 'grafana_new_password', None)
        if not isinstance(username, str) or not username:
            username = prompt_text('Utilisateur Grafana actuel', default='admin')
        if not isinstance(old, str) or not old:
            old = secret('Mot de passe Grafana actuel')
        if not isinstance(new, str) or not new:
            new = password_twice('Nouveau mot de passe Grafana')
        code, _ = grafana_api(username, old, '/api/user')
        if code != 200:
            raise SetupError('Identifiant Grafana existant incorrect ; rien n’a été modifié')
        code, _ = grafana_api(username, old, '/api/user/password', 'PUT',
                              {'oldPassword': old, 'newPassword': new})
        if code != 200 or grafana_api(username, new, '/api/user')[0] != 200:
            raise SetupError('Échec de vérification du nouveau mot de passe Grafana')
        out('✓', 'Mot de passe Grafana modifié et validé')
        return username, new
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
    import observability_dashboards as dashboards
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
        if (override_changed or ds_changed or dashboards.needs_update(cfg().datasource_uid,cfg().jenkins_job)) and not yesno('Grafana existant : confirmer mise à jour, source et redémarrage ?', False, noninteractive=args.non_interactive):
            raise SetupError('Grafana existant conservé : modification non autorisée')
    changed_override = owned_write(GRAFANA_DROPIN, unit_override_grafana(args.grafana_domain))
    run(['systemctl', 'daemon-reload'])
    fresh = (not exists or not Path('/var/lib/grafana/grafana.db').exists() or
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
    changed = dashboards.provision(sys.modules[__name__]) or changed
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
        dashboards.verify(sys.modules[__name__],auth)
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
        from jenkins_firstboot import auth_file_if_available
        authargs = SimpleNamespace(auth_file=auth_file_if_available(), interactive=True)
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
        from jenkins_firstboot import METRICS_AUTH, _secure_file
        if METRICS_AUTH.is_file():
            # Only the new AUTOKOM-controlled Jenkins gets an automatically
            # provisioned, unprivileged Metrics.VIEW account. Never reuse its
            # administrator token for Prometheus scraping.
            _secure_file(METRICS_AUTH)
            try:
                username, password = METRICS_AUTH.read_text(encoding='utf-8').strip().split(':', 1)
            except (ValueError, OSError):
                raise SetupError('Secret du compte Jenkins Metrics AUTOKOM invalide') from None
            if username != 'autokom-metrics' or len(password) < 20:
                raise SetupError('Compte Jenkins Metrics AUTOKOM non conforme')
            out('›', 'Authentification de l’exporter avec le compte Metrics.VIEW AUTOKOM')
        else:
            # Foreign/previous Jenkins controllers retain their established
            # credentials; AUTOKOM must not reset or infer those secrets.
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


def prompt_domain(label, *, max_attempts=3, expected_ipv4=None, unavailable=()):
    """Valide chaque saisie AVANT de passer à la question suivante.

    Refuse sans effet de bord : domaine vide, syntaxe erronée, DNS non résolu,
    A/AAAA qui ne pointent pas vers cette VM, domaine déjà utilisé par un autre
    composant. Le compteur de tentatives reste volontairement invisible.
    """
    for _ in range(max_attempts):
        response = prompt_text(
            label, hint='Nom DNS complet (sans https://). Contrôle DNS/IP de la VM (GCP ou AWS) immédiat.')
        try:
            domain = validate_domain(response)
            if not domain:
                raise SetupError('Domaine obligatoire après sélection « Oui »')
            if domain in unavailable:
                raise SetupError(f'{domain} est déjà réservé à un autre service AUTOKOM')
            with Activity(f'Vérification DNS / IP publique GCP/AWS · {domain}', delay=0.4):
                addresses = check_direct_ec2_domain(domain, expected_ipv4=expected_ipv4)
        except (SetupError, DomainRoutingError) as exc:
            out('!', f'{label} : {exc}')
            continue
        out('✓', f'{domain} → {", ".join(addresses)} · DNS conforme à cette instance')
        return domain
    raise SetupError(f'{label} : domaine non validé après trois saisies ; '
                     'corriger les enregistrements DNS A/AAAA et relancer AUTOKOM')


def _certificate_email_for(args, domain):
    """Un seul contact ACME facultatif pour l'ensemble de l'installation.

    La décision (même vide) est conservée dans args pendant toute la commande.
    En simulation ou avec un certificat déjà valable, aucune saisie nécessaire.
    """
    if (not domain or getattr(args, 'mode', 'check') != 'apply' or
            getattr(args, '_cert_email_decided', False)):
        return
    if args.email:
        # Valeur déjà fournie via le fichier de configuration ou --email.
        args._cert_email_decided = True
        return
    if existing_vhosts(domain) or valid_cert(domain):
        # Un vhost existant n'est pas réécrit ; un certificat valide est réutilisé.
        return
    args._cert_email_decided = True
    for _ in range(3):
        value = prompt_text('E-mail Let’s Encrypt (facultatif, Entrée pour ignorer)',
                            hint='Une seule saisie pour Prometheus et Grafana').strip()
        if not value:
            args.email = ''
            out('!', 'Sans e-mail : pas d’alerte Let’s Encrypt sur l’expiration/révocation du certificat')
            args._cert_email_warning_emitted = True
            return
        if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            args.email = value
            return
        out('!', 'Format d’e-mail invalide ; corriger ou laisser vide')
    raise SetupError('E-mail Let’s Encrypt invalide après trois saisies')


def grafana_requires_initial_admin():
    """Détecter installation nouvelle ou admin Grafana initial non remplacé."""
    if not package_installed('grafana'):
        return True
    if not Path('/var/lib/grafana/grafana.db').exists():
        return True
    return active('grafana-server') and grafana_api('admin', 'admin', '/api/user')[0] == 200


def interactive_options(args):
    """Deux formulaires complets, dans l'ordre Prometheus puis Grafana.

    Les secrets restent en mémoire (jamais dans le plan, les logs ni les URLs).
    --check ne demande pas de secrets ; les validations DNS sont immédiates.
    """
    if args.non_interactive:
        args.prometheus_domain = validate_domain(args.prometheus_domain)
        args.grafana_domain = validate_domain(args.grafana_domain)
        return

    section('Prometheus · paramètres', 'Domaine · TLS · accès HTTPS', tone='prometheus')
    if not args.prometheus_domain and yesno('Prometheus dispose-t-il d’un domaine HTTPS ?', False):
        args.prometheus_domain = prompt_domain(
            'Domaine Prometheus', expected_ipv4=args.public_ip,
            unavailable={args.grafana_domain} if args.grafana_domain else ())
    if args.prometheus_domain:
        _certificate_email_for(args, args.prometheus_domain)
        # Ces informations sont demandées avant le formulaire Grafana.
        interactive_prometheus_password(args)
    else:
        out('✓', f'Prometheus : accès privé {cfg().prometheus_base}')

    section('Grafana · paramètres', 'Domaine · TLS · administration', tone='grafana')
    if not args.grafana_domain and yesno('Grafana dispose-t-il d’un domaine HTTPS ?', False):
        args.grafana_domain = prompt_domain(
            'Domaine Grafana', expected_ipv4=args.public_ip,
            unavailable={args.prometheus_domain} if args.prometheus_domain else ())
    if args.grafana_domain:
        _certificate_email_for(args, args.grafana_domain)
    else:
        out('✓', f'Grafana : accès privé {cfg().grafana_base}')

    # Collecter tous les champs Grafana avant tout apt-get / Certbot.
    first_credentials = grafana_requires_initial_admin()
    if getattr(args, 'mode', 'check') == 'apply':
        if first_credentials:
            username = args.grafana_user or prompt_text(
                'Administrateur Grafana', default='admin', hint='Compte local, pas un token API')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{2,39}', username):
                raise SetupError('Identifiant administrateur Grafana invalide')
            args.grafana_user = username
            args.grafana_new_password = password_twice('Grafana', args.non_interactive)
        else:
            args.grafana_rotate = (yesno('Modifier le mot de passe Grafana existant ?', False)
                                   if getattr(args, 'reconfigure', False) else False)
            if not getattr(args, 'reconfigure', False):
                out('✓', 'Grafana : administrateur existant conservé (aucune ressaisie)')
            if args.grafana_rotate:
                args.grafana_rotate_user = prompt_text('Utilisateur Grafana actuel', default='admin')
                args.grafana_old_password = secret('Mot de passe Grafana actuel')
                args.grafana_new_password = password_twice('Nouveau mot de passe Grafana')
            else:
                out('✓', 'Grafana existant : comptes conservés')

    if args.prometheus_domain and args.prometheus_domain == args.grafana_domain:
        raise SetupError('Prometheus et Grafana ne peuvent pas utiliser le même domaine')

def preflight_credentials(args):
    """Empêcher une application partielle faute de secrets avant toute mutation."""
    if args.mode != 'apply':
        return
    if (args.prometheus_domain and not PROM_HTPASSWD.exists() and
            not args.prometheus_password):
        raise SetupError('Authentification HTTPS Prometheus requise avant installation : '
                         'lancer en mode interactif')
    if (grafana_requires_initial_admin() and not getattr(args, 'grafana_new_password', '')):
        raise SetupError('Mot de passe administrateur Grafana requis avant installation : '
                         'lancer en mode interactif')


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
    extra_ports=[]
    if getattr(args, 'setup_node', False):
        extra_ports.append((9100,'prometheus-node-exporter'))
    if getattr(args, 'setup_alertmanager', False):
        extra_ports.append((9093,'prometheus-alertmanager'))
    for port, expected in ((cfg().prometheus_port, 'prometheus'),
                           (cfg().grafana_port, 'grafana-server'), *extra_ports):
        if listener(port) and not active(expected):
            raise SetupError(f'TCP/{port} est déjà occupé par un service inconnu : ne pas écraser')


def preflight_domains(args):
    """Contrôles réseau avant tout redémarrage/installation/Certbot.

    Sur VM directe GCP ou AWS, DNS A doit pointer vers l’IP publique détectée.
    Pour les déploiements ALB/CDN, préférer un profil d'infrastructure dédié.
    """
    requested = [x for x in (args.prometheus_domain, args.grafana_domain) if x]
    if not requested:
        return
    out('›', 'Vérification DNS des domaines et de l’IP publique GCP/AWS…')
    for domain in requested:
        with Activity(f'DNS / IP publique GCP/AWS · {domain}', delay=0.4):
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
        out('!', 'HTTP-01 doit aussi être joignable depuis Internet (pare-feu VPC/SG et UFW) ; Certbot le vérifiera')


def interactive_prometheus_password(args):
    """Les secrets sont demandés seulement APRÈS validation des domaines."""
    if (args.prometheus_domain and getattr(args, 'mode', 'check') == 'apply' and
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
    parser.add_argument('--reconfigure', action='store_true', help='Redemander explicitement les domaines et comptes')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--prometheus-domain', default=None)
    parser.add_argument('--grafana-domain', default=None)
    parser.add_argument('--email', default=None)
    parser.add_argument('--grafana-user', default='')
    parser.add_argument('--public-ip', default=None,
                        help='IPv4 publique directement attachée, si métadonnées GCP/AWS indisponibles (jamais privée)')
    parser.add_argument('--config', default=os.environ.get('AUTOKOM_OBSERVABILITY_CONFIG'),
                        help='Fichier INI privé de surcharges non sensibles')
    parser.add_argument('--set', action='append', default=[], metavar='SECTION.KEY=VALUE',
                        help='Surcharge validée pour une seule exécution (répétable)')
    parser.add_argument('--show-config', action='store_true',
                        help='Afficher les paramètres et URLs déduits sans sudo ni modification')
    args = parser.parse_args(argv)
    global SETTINGS
    SETTINGS = load_settings(custom_path=args.config, changes=args.set)
    # Reuse validated AUTOKOM-owned domains on subsequent runs; the operator
    # should not have to re-enter domains after installing Node Exporter later.
    stored = previous_state() if args.mode == 'apply' else {}
    pending = previous_selection() if args.mode == 'apply' and not args.reconfigure else {}
    def adopted_domain(kind, existing):
        if existing:
            return existing
        last=pending.get(f'{kind}_domain','') or stored.get(f'{kind}_domain','')
        if last:
            return validate_domain(last)
        # Upgrade from pre-v16 installations where domains weren't persisted.
        folder=Path('/etc/nginx/sites-enabled')
        if not folder.is_dir():
            return ''
        found=[]
        for entry in folder.glob(f'autokom-{kind}-*.conf'):
            domain=entry.name[len(f'autokom-{kind}-'):-5]
            try:
                if validate_domain(domain): found.append(domain)
            except SetupError:
                continue
        return found[0] if len(found)==1 else ''
    SETTINGS = validate_settings(replace(SETTINGS,
        prometheus_domain=(('' if args.reconfigure else adopted_domain('prometheus', SETTINGS.prometheus_domain))
                           if args.prometheus_domain is None else args.prometheus_domain),
        grafana_domain=(('' if args.reconfigure else adopted_domain('grafana', SETTINGS.grafana_domain))
                        if args.grafana_domain is None else args.grafana_domain),
        email=(pending.get('email', SETTINGS.email) if args.email is None else args.email)))
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
    args.grafana_new_password = ''
    args.grafana_user = args.grafana_user or pending.get('grafana_user', '')
    args.setup_node = False
    args.setup_alertmanager = False
    args.smtp = None
    args._selection = pending
    # A stored empty ACME email is an intentional choice; never ask for it
    # again unless the administrator explicitly enters reconfiguration mode.
    args._cert_email_decided = bool(
        args.mode == 'apply' and not args.reconfigure and pending and
        pending.get('email_decided', 'email' in pending))
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
    section('Observabilité', 'Jenkins → Prometheus → Grafana')
    interactive_options(args)
    from observability_extras import collect_options
    collect_options(sys.modules[__name__], args)
    SETTINGS = validate_settings(replace(SETTINGS, prometheus_domain=args.prometheus_domain,
                                          grafana_domain=args.grafana_domain, email=args.email))
    out('›', f'Ports actifs ciblés : Jenkins {cfg().jenkins_port} · Prometheus {cfg().prometheus_port} · Grafana {cfg().grafana_port}')
    preflight(args)
    # Persist validated, non-secret wizard answers before external DNS/ACME
    # probes: their failure must never force the operator to repeat the form.
    save_selection(args)
    preflight_domains(args)
    preflight_credentials(args)
    if args.mode == 'check':
        check_only(args)
        return 0
    # Les formulaires Prometheus/Grafana sont terminés avant les modifications.
    # En mode non interactif, le garde-fou du secret Prometheus reste ci-dessous.
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
    # Additional services are strictly opt-in and do not alter existing accounts.
    from observability_extras import ensure_node, ensure_alertmanager
    ensure_node(sys.modules[__name__], args)
    from observability_docker import ensure_cadvisor
    ensure_cadvisor(sys.modules[__name__], args)
    ensure_alertmanager(sys.modules[__name__], args)
    # Reconcile only a recognizable, unchanged Ubuntu welcome default; never
    # steal ports or replace a custom default. ACME DNS-01 does not prove
    # Nginx's HTTP/HTTPS host routing.
    if args.prometheus_domain or args.grafana_domain:
        from autokom_ingress_health import reconcile_catchall, IngressError
        try:
            outcome = reconcile_catchall()
        except IngressError as exc:
            raise SetupError('Routage Nginx par défaut : ' + str(exc)) from exc
        out('✓', 'Nginx : '+outcome)
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
    # Real host-routed SNI/certificate test and HTTP -> HTTPS redirect. Never
    # declare a full installation successful merely because services are active
    # and Certbot returned 0. This is local ingress, not a remote GCP VPC test.
    if args.prometheus_domain or args.grafana_domain:
        from autokom_ingress_health import verify_observability, IngressError
        try:
            verify_observability([('prometheus', args.prometheus_domain),
                                  ('grafana', args.grafana_domain)])
        except IngressError as exc:
            raise SetupError('Vérification finale du reverse proxy : '+str(exc)) from exc
        out('✓', 'Nginx : HTTPS/SNI local et redirections HTTP validés (VPC externe non vérifié)')
    save_state()
    out('✓', 'AUTOKOM observabilité configurée : services locaux, Nginx et TLS vérifiés')
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
