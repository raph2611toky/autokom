#!/usr/bin/env python3
"""AUTOKOM phase 2: Jenkins and database service orchestration.
Run from scripts/install/05-services.sh; no third-party Python libraries.
"""
import argparse
import ipaddress
import grp
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from menu import pick, ask

ROOT = Path(__file__).resolve().parents[2]
MANAGED = '# Managed by AUTOKOM: phase-2'
SECRETS = Path('/etc/autokom/secrets')


def say(text, mark='›'):
    colors = {'✓': '32', '+': '36', '!': '33', '✗': '31', '›': '36'}
    color = colors.get(mark, '36') if sys.stdout.isatty() and not os.environ.get('NO_COLOR') else '0'
    prefix = f'\033[{color}m{mark}\033[0m' if color != '0' else mark
    print(f'  {prefix}  {text}', flush=True)


def fail(message):
    say(message, '✗')
    raise RuntimeError(message)


def run(args, *, input_text=None, capture=True, check=True, env=None):
    ret = subprocess.run(args, input=input_text, text=True, capture_output=capture, check=False, env=env)
    if check and ret.returncode:
        output = (ret.stderr or ret.stdout or '').strip()
        raise RuntimeError(f"Commande en échec ({args[0]}): {output[-1200:]}")
    return ret


def atomic_write(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return False
    temp = path.parent / f'.{path.name}.{os.getpid()}.tmp'
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def backup(path):
    path = Path(path)
    if not path.exists():
        return None
    dest = path.with_name(f'{path.name}.autokom-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}')
    shutil.copy2(path, dest)
    return dest


def valid_domain(domain):
    if not re.fullmatch(r'(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+', domain.lower()):
        raise ValueError('Nom de domaine DNS invalide')
    return domain.lower()


def cidrs(value):
    if not value.strip():
        return []
    items = [p.strip() for p in value.split(',') if p.strip()]
    return [str(ipaddress.ip_network(p, strict=False)) for p in items]


def check_user_input(options):
    options.domain = valid_domain(options.domain) if options.domain else ''
    options.allow = cidrs(options.allow) if isinstance(options.allow, str) else options.allow
    if options.db in ('postgresql', 'mysql') and options.bind in ('private', 'all') and not options.allow and options.bind != 'all':
        raise ValueError('Au moins un réseau CIDR autorisé est requis')
    if options.bind == 'all' and not options.unsafe_all:
        raise ValueError('L’écoute globale nécessite --unsafe-all et une confirmation de sécurité')
    if options.tls == 'provided' and (not options.cert or not options.key):
        raise ValueError('Certificat et clé privée obligatoires pour --tls provided')
    if options.tls == 'certbot' and options.domain and not options.email:
        raise ValueError('Adresse email requise pour Certbot')
    if options.db == 'mysql' and any(ipaddress.ip_network(c).version == 6 and ipaddress.ip_network(c).prefixlen != 128 for c in options.allow):
        raise ValueError('MySQL : les réseaux IPv6 CIDR non unitaires ne sont pas pris en charge')
    if options.db == 'oracle' and options.bind != 'local':
        raise ValueError('Oracle Free : ce module limite actuellement l’écoute à localhost')
    return options


def interactive(options):
    say('Installation interactive — ↑/↓, Entrée ou chiffre 1–9')
    if options.db is None:
        option = pick('Base de données', ['PostgreSQL (recommandé)', 'MySQL', 'Oracle Database Free (Docker, serveur puissant)', 'Aucune'])
        options.db = ['postgresql', 'mysql', 'oracle', 'none'][option-1]
    if options.db != 'none' and options.bind is None:
        selected = pick('Écoute réseau de la base', ['Locale uniquement (recommandé)', 'Réseau privé / IP autorisées', 'Toutes interfaces (risqué)'])
        options.bind = ['local','private','all'][selected-1]
    if options.bind in ('private','all') and options.db != 'none' and not options.allow:
        options.allow = ask('Réseaux clients autorisés (CIDR séparés par virgules, ex: 10.0.0.0/24)', '0.0.0.0/0' if options.bind == 'all' else '')
    if options.bind == 'all':
        confirm = ask('ATTENTION : accès réseau large, saisir OUVRIR pour confirmer')
        options.unsafe_all = confirm == 'OUVRIR'
    if options.docker_access is None:
        selected = pick('Autoriser Jenkins à contrôler le démon Docker ?', ['Non (isolation maximale)', 'Oui (jenkins devient root-équivalent)'])
        options.docker_access = (selected == 2)
        if options.docker_access:
            options.docker_access = ask('Saisir DOCKER-ROOT pour confirmer') == 'DOCKER-ROOT'
    if options.domain is None:
        domain_choice = pick('Reverse proxy Jenkins', ['Configurer un domaine', 'Sans domaine (accès localhost/tunnel SSH)'])
        options.domain = ask('Domaine Jenkins, ex: jenkins.example.com') if domain_choice == 1 else ''
    if options.domain and options.tls is None:
        selected = pick('HTTPS Jenkins', ['Certbot / Let’s Encrypt', 'Certificat existant', 'HTTP uniquement (non recommandé)'])
        options.tls = ['certbot','provided','none'][selected-1]
    if options.domain and options.tls == 'certbot' and not options.email:
        options.email = ask('E-mail Let’s Encrypt')
    if options.domain and options.tls == 'provided':
        options.cert = ask('Chemin certificat fullchain.pem')
        options.key = ask('Chemin clé privée .pem')
    return options


def effective_option_defaults(options):
    options.bind = options.bind or 'local'
    options.docker_access = options.docker_access if options.docker_access is not None else False
    options.domain = options.domain or ''
    options.tls = options.tls or 'none'
    options.email = options.email or ''
    options.allow = options.allow or ''
    return check_user_input(options)


def memory_kib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemTotal:'):
            return int(line.split()[1])
    return 0


def preflight(options):
    if not Path('/etc/os-release').exists():
        fail('OS non reconnu')
    if options.db == 'oracle':
        if memory_kib() < 12 * 1024 * 1024:
            fail('Oracle nécessite ici au moins 12 Gio de RAM totale (7,6 Gio détectés auparavant) ; choisir PostgreSQL/MySQL ou une VM plus grande')
        if not shutil.which('docker'):
            fail('Oracle Free nécessite Docker installé')
    if options.domain and options.tls == 'provided':
        for p in (options.cert, options.key):
            if not Path(p).is_file():
                fail(f'Fichier TLS introuvable : {p}')
    if options.domain:
        existing = Path(f'/etc/nginx/sites-enabled/{options.domain}.conf')
        if existing.exists() and MANAGED not in existing.resolve().read_text(errors='ignore'):
            say('VirtualHost existant détecté : AUTOKOM le conservera intact.', '!')
    say(f'Jenkins : présent={shutil.which("jenkins") is not None} ; domaine={options.domain or "aucun"}')
    say(f'Base : {options.db} ; bind={options.bind} ; autorisations={", ".join(options.allow) or "localhost"}')
    if options.bind != 'local':
        say('Les Security Groups AWS/GCP et les pare-feu doivent aussi limiter les clients ; ne sont pas modifiés automatiquement.', '!')


def ansible_install(options):
    if not shutil.which('ansible-playbook'):
        fail('Ansible absent : lancer scripts/bootstrap/01-install-ansible.sh')
    extra = {'autokom_db_engine': options.db, 'autokom_jenkins_docker': options.docker_access}
    args = [
        'ansible-playbook', '-i', str(ROOT/'ansible/inventories/local/hosts.ini'),
        str(ROOT/'ansible/playbooks/02-services.yml'), '-e', json.dumps(extra),
    ]
    if options.mode == 'check':
        args.append('--check')
    env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT/'ansible/ansible.cfg'),
               ANSIBLE_CALLBACK_PLUGINS=str(ROOT/'ansible/plugins/callback'),
               ANSIBLE_STDOUT_CALLBACK='autokom', AUTOKOM_CHECK_MODE='1' if options.mode == 'check' else '0')
    say('Lancement Ansible (callback AUTOKOM)')
    result = subprocess.call(args, env=env)
    if result:
        fail(f'Ansible a échoué (code {result})')


def write_secret(name):
    SECRETS.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS, 0o700)
    path = SECRETS / name
    if path.exists():
        if not path.is_file() or path.is_symlink():
            fail(f'Secret non régulier : {path}')
        return path.read_text().strip()
    token = secrets.token_urlsafe(36)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token + '\n')
    return token


def postgres_config(options):
    base = Path('/etc/postgresql')
    versions = sorted([d for d in base.glob('*/main') if d.is_dir()], key=lambda p: [int(z) for z in p.parent.name.split('.')], reverse=True)
    if not versions:
        fail('PostgreSQL installé, mais aucun cluster main détecté')
    cfg = versions[0]
    main_conf = cfg / 'postgresql.conf'
    hba = cfg / 'pg_hba.conf'
    listen = '127.0.0.1' if options.bind == 'local' else '0.0.0.0'
    # Preserve other settings, override only the selected parameter.
    current = main_conf.read_text()
    changed = re.sub(r'^[ \t]*#?[ \t]*listen_addresses[ \t]*=.*$', f"listen_addresses = '{listen}'  {MANAGED}", current, flags=re.M)
    if changed == current and 'listen_addresses' not in current:
        changed += f"\nlisten_addresses = '{listen}'  {MANAGED}\n"
    if changed != current:
        backup(main_conf)
        atomic_write(main_conf, changed)
    start = '# BEGIN AUTOKOM ACCESS'
    end = '# END AUTOKOM ACCESS'
    networks = ['127.0.0.1/32', '::1/128']
    networks.extend(options.allow)
    lines = [start, '# Only AUTOKOM-managed application account; no global trust']
    lines.extend(f'host autokom_apps autokom_app {net} scram-sha-256' for net in sorted(set(networks)))
    lines.extend(['host all autokom_app 0.0.0.0/0 reject', 'host all autokom_app ::/0 reject', end])
    hba_text = hba.read_text()
    hba_text = re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\n?', '', hba_text, flags=re.S)
    hba_next = '\n'.join(lines) + '\n' + hba_text
    if hba_next != hba.read_text():
        backup(hba)
        atomic_write(hba, hba_next)
    # Create account via local peer authentication; avoid passwords in argv or logs.
    role_exists = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc', "SELECT 1 FROM pg_roles WHERE rolname='autokom_app'" ]).stdout.strip()
    if role_exists and not (SECRETS / 'postgresql-autokom-app.password').exists():
        fail('Compte autokom_app existant mais secret non géré : ne pas écraser le compte')
    if not role_exists:
        password = write_secret('postgresql-autokom-app.password')
        run(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1'], input_text=f"CREATE ROLE autokom_app LOGIN PASSWORD '{password}';\n")
    db_exists = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc', "SELECT 1 FROM pg_database WHERE datname='autokom_apps'" ]).stdout.strip()
    if not db_exists:
        run(['runuser', '-u', 'postgres', '--', 'createdb', '-O', 'autokom_app', 'autokom_apps'])
    run(['runuser','-u','postgres','--','psql','-Atqc', 'SELECT pg_reload_conf()'])
    # listen_addresses requires restart, only on change.
    if listen not in run(['runuser','-u','postgres','--','psql','-Atqc','SHOW listen_addresses']).stdout.strip():
        run(['systemctl', 'restart', 'postgresql'])
    say('PostgreSQL : compte applicatif, base et accès clients configurés', '✓')
    say('Secret : /etc/autokom/secrets/postgresql-autokom-app.password (root uniquement)')


def mysql_config(options):
    conf = Path('/etc/mysql/mysql.conf.d/90-autokom.cnf')
    listen = '127.0.0.1' if options.bind == 'local' else '0.0.0.0'
    # UNIX socket auth for root is expected on Ubuntu packages; don't erase user root password.
    if run(['mysql','--protocol=socket','-uroot','-Nse','SELECT 1'], check=False).returncode:
        fail('MySQL root socket authentication unavailable : fournir les identifiants administrateur manuellement')
    if conf.exists():
        backup(conf)
    changed = atomic_write(conf, f'{MANAGED}\n[mysqld]\nbind-address = {listen}\n', 0o644)
    if changed:
        run(['systemctl', 'restart', 'mysql'])
    existing = run(['mysql', '--protocol=socket', '-uroot', '-Nse', "SELECT COUNT(*) FROM mysql.user WHERE user='autokom_app'"]).stdout.strip()
    if int(existing or '0') > 0 and not (SECRETS / 'mysql-autokom-app.password').exists():
        fail('Un utilisateur autokom_app existe mais son secret AUTOKOM est absent : opération refusée')
    password = write_secret('mysql-autokom-app.password')
    hosts = ['localhost']
    for subnet in options.allow:
        net = ipaddress.ip_network(subnet, strict=False)
        if net.version == 4:
            if net.prefixlen == 0:
                hosts.append('%')
            else:
                hosts.append(f'{net.network_address}/{net.netmask}' if net.prefixlen != 32 else str(net.network_address))
        elif net.prefixlen == 128:
            hosts.append(str(net.network_address))
        else:
            fail('MySQL : CIDR IPv6 étendu non supporté pour les comptes clients dans ce module')
    if options.bind == 'all' and not options.allow:
        hosts.append('%')
    stmts = ['CREATE DATABASE IF NOT EXISTS `autokom_apps` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;']
    for host in sorted(set(hosts)):
        # host never contains apostrophes: validated by ipaddress or literal localhost.
        stmts += [f"CREATE USER IF NOT EXISTS 'autokom_app'@'{host}' IDENTIFIED BY '{password}';",
                  f"GRANT ALL PRIVILEGES ON `autokom_apps`.* TO 'autokom_app'@'{host}';"]
    run(['mysql','--protocol=socket','-uroot'], input_text='\n'.join(stmts) + '\n')
    say('MySQL : base, compte applicatif et restrictions client configurés', '✓')
    say('Secret : /etc/autokom/secrets/mysql-autokom-app.password (root uniquement)')


def oracle_config(options):
    name = 'autokom-oracle'
    if run(['docker','container','inspect',name], check=False).returncode == 0:
        say('Oracle Free : conteneur déjà présent, aucune recréation', '✓')
        return
    # Images Oracle may require acceptance of use terms; deliberately no unreviewed licensing bypass.
    say('Oracle Free utilise l’image officielle et les conditions d’utilisation Oracle.', '!')
    secret = write_secret('oracle-free.password')
    config = SECRETS/'oracle.env'
    atomic_write(config, f'ORACLE_PWD={secret}\n', 0o600)
    run(['docker','volume','create','autokom_oracle_data'])
    image = 'container-registry.oracle.com/database/free:latest-lite'
    say('Téléchargement de l’image Oracle Free : cela peut être volumineux')
    run(['docker','pull',image])
    run(['docker','run','-d','--name',name,'--restart','unless-stopped',
         '--publish','127.0.0.1:1521:1521', '--env-file',str(config),
         '--volume','autokom_oracle_data:/opt/oracle/oradata',image])
    say('Oracle Free : conteneur démarré sur localhost:1521 ; initialisation interne à surveiller', '✓')
    say('Attention : le mot de passe passé au conteneur reste visible aux administrateurs Docker.', '!')



def configure_jenkins_url(options):
    """Set canonical URL only if empty/default, never replace an existing custom URL."""
    if not options.domain:
        return
    destination = Path('/var/lib/jenkins/init.groovy.d/90-autokom-location.groovy')
    url = ('https://' if options.tls != 'none' else 'http://') + options.domain + '/'
    # The domain is validated before interpolation into Groovy source.
    groovy = f'''// {MANAGED}
import jenkins.model.JenkinsLocationConfiguration

def location = JenkinsLocationConfiguration.get()
def current = location.getUrl()
if (current == null || current.trim() == '' ||
    current == 'http://localhost:8080/' || current == 'http://127.0.0.1:8080/') {{
    location.setUrl('{url}')
    location.save()
}}
'''
    if destination.exists() and MANAGED not in destination.read_text(errors='ignore'):
        fail('Emplacement script Jenkins déjà géré par un tiers : opération refusée')
    changed = atomic_write(destination, groovy, 0o644)
    if changed:
        run(['chown', 'jenkins:jenkins', str(destination)])
        os.environ['AUTOKOM_JENKINS_URL_CHANGED'] = '1'
        say('URL canonique Jenkins préparée pour le prochain redémarrage', '✓')


def configure_jenkins_listen():
    path = Path('/etc/systemd/system/jenkins.service.d/20-autokom-loopback.conf')
    content = '[Service]\nEnvironment="JENKINS_LISTEN_ADDRESS=127.0.0.1"\n'
    if path.exists() and MANAGED not in path.read_text():
        fail(f'Override Jenkins non AUTOKOM existant : {path}')
    content = MANAGED + '\n' + content
    changed = atomic_write(path, content)
    restart_needed = changed or os.environ.get('AUTOKOM_JENKINS_URL_CHANGED') == '1'
    if changed:
        run(['systemctl','daemon-reload'])
    pid = run(['systemctl','show','-p','MainPID','--value','jenkins']).stdout.strip()
    if pid.isdigit() and int(pid) > 0:
        try:
            docker_gid = grp.getgrnam('docker').gr_gid
            group_line = next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('Groups:'))
            if docker_gid not in [int(x) for x in group_line.split()[1:]] and os.environ.get('AUTOKOM_JENKINS_DOCKER') == '1':
                restart_needed = True
        except (KeyError, FileNotFoundError, StopIteration):
            pass
    if restart_needed:
        run(['systemctl','restart','jenkins'])
    if run(['systemctl','is-active','--quiet','jenkins'], check=False).returncode:
        fail('Jenkins ne démarre pas, vérifier journalctl -u jenkins')
    listeners = run(['ss','-ltn']).stdout
    if any(re.search(r'(?:0\.0\.0\.0|\*|\[::\]):8080\s', line) for line in listeners.splitlines()):
        fail('Jenkins écoute encore sur une interface publique: vérifier systemctl cat jenkins et le port 8080')
    say('Jenkins : écoute limitée à 127.0.0.1:8080', '✓')


def configure_proxy(options):
    if not options.domain:
        say('Aucun domaine : Jenkins accessible via tunnel SSH localhost', '›')
        return
    domain = options.domain
    available = Path('/etc/nginx/sites-available') / f'{domain}.conf'
    enabled = Path('/etc/nginx/sites-enabled') / f'{domain}.conf'
    if available.exists() or enabled.exists():
        existing_path = enabled.resolve() if enabled.exists() else available
        if MANAGED not in existing_path.read_text(errors='ignore'):
            say(f'Domaine {domain} déjà configuré hors AUTOKOM ; configuration conservée.', '!')
            return
        if run(['nginx', '-t'], check=False).returncode == 0:
            say(f'Domaine {domain} déjà géré par AUTOKOM ; configuration conservée.', '✓')
            return
    if options.tls == 'certbot':
        try:
            ips = {x[4][0] for x in socket.getaddrinfo(domain, 80, family=socket.AF_INET)}
        except socket.gaierror:
            fail(f'DNS indisponible pour {domain}; demander un DNS A valide avant Certbot')
        if not ips:
            fail('Pas d’enregistrement IPv4 A résolu')
    if options.tls == 'provided':
        cert = Path(options.cert).resolve()
        key = Path(options.key).resolve()
        # Require certificate and key correspond, and certificate is valid for domain.
        run(['openssl','x509','-in',str(cert),'-checkend','86400','-noout'])
        run(['openssl','x509','-in',str(cert),'-noout','-checkhost',domain])
        pub_cert = run(['openssl','x509','-in',str(cert),'-pubkey','-noout']).stdout
        pub_key = run(['openssl','pkey','-in',str(key),'-pubout']).stdout
        if pub_cert.strip() != pub_key.strip():
            fail('La clé privée ne correspond pas au certificat')
        tls_blocks = f'''    listen 443 ssl;\n    ssl_certificate {cert};\n    ssl_certificate_key {key};\n'''
    else:
        tls_blocks = '    listen 80;\n'
    if options.tls == 'provided':
        http_redirect = f'server {{ listen 80; server_name {domain}; return 301 https://$host$request_uri; }}\n'
    else:
        http_redirect = ''
    content = f'''{MANAGED}
# Jenkins upstream limited to localhost. Keep private management port closed in the cloud firewall.
{http_redirect}server {{
{tls_blocks}    server_name {domain};
    client_max_body_size 64m;
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $autokom_jenkins_connection;
        proxy_read_timeout 90s;
        proxy_request_buffering off;
    }}
}}
'''
    map_file = Path('/etc/nginx/conf.d/99-autokom-jenkins-map.conf')
    map_content = f'''{MANAGED}
map $http_upgrade $autokom_jenkins_connection {{
    default upgrade;
    '' close;
}}
'''
    if map_file.exists() and MANAGED not in map_file.read_text():
        fail('La variable de map Nginx est déjà gérée par une autre configuration')
    map_was_new = not map_file.exists()
    old_content = available.read_text() if available.exists() else None
    old_link = enabled.is_symlink()
    try:
        atomic_write(map_file, map_content)
        if available.exists():
            backup(available)
        atomic_write(available, content)
        if not enabled.exists():
            enabled.symlink_to(available)
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])
        if options.tls == 'certbot':
            # Certbot validates ACME. It edits its managed vhost and sets up renewal.
            run(['certbot','--nginx','--non-interactive','--agree-tos','--redirect',
                 '--email',options.email,'-d',domain])
            run(['nginx','-t'])
    except Exception:
        if map_was_new:
            map_file.unlink(missing_ok=True)
        if old_content is None:
            available.unlink(missing_ok=True)
            if not old_link and enabled.is_symlink():
                enabled.unlink(missing_ok=True)
        else:
            atomic_write(available, old_content)
        run(['nginx','-t'], check=False)
        run(['systemctl','reload','nginx'], check=False)
        raise
    say(f'Jenkins : reverse proxy {domain} configuré ({options.tls})', '✓')


def main():
    parser = argparse.ArgumentParser(description='AUTOKOM Jenkins / Database module')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_const', const='check', dest='mode')
    group.add_argument('--apply', action='store_const', const='apply', dest='mode')
    parser.add_argument('--db', choices=('postgresql','mysql','oracle','none'))
    parser.add_argument('--bind', choices=('local','private','all'))
    parser.add_argument('--allow', default='', help='IPv4/IPv6 CIDR clients, separated by comma')
    parser.add_argument('--unsafe-all', action='store_true')
    parser.add_argument('--docker-access', choices=('yes','no'))
    parser.add_argument('--domain', default=None)
    parser.add_argument('--tls', choices=('certbot','provided','none'))
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--email')
    parser.add_argument('--non-interactive', action='store_true')
    args = parser.parse_args()
    args.docker_access = {'yes': True, 'no': False}.get(args.docker_access)
    if os.geteuid() != 0:
        fail('Exécuter en root/sudo')
    if not args.non_interactive:
        args = interactive(args)
    elif args.db is None or args.docker_access is None or args.domain is None:
        parser.error('--non-interactive exige --db, --docker-access, --domain (chaîne vide autorisée)')
    args = effective_option_defaults(args)
    print('\n  \033[1;36mAUTOKOM · Jenkins & Databases\033[0m')
    say('Mode : ' + ('Simulation' if args.mode == 'check' else 'Installation'))
    preflight(args)
    ansible_install(args)
    if args.mode == 'check':
        say('Simulation terminée. Aucune configuration Jenkins ou DB modifiée.', '✓')
        return
    os.environ['AUTOKOM_JENKINS_DOCKER'] = '1' if args.docker_access else '0'
    configure_jenkins_url(args)
    configure_jenkins_listen()
    if args.db == 'postgresql':
        postgres_config(args)
    elif args.db == 'mysql':
        mysql_config(args)
    elif args.db == 'oracle':
        oracle_config(args)
    configure_proxy(args)
    init = Path('/var/lib/jenkins/secrets/initialAdminPassword')
    if init.exists():
        say('Jenkins : initialisation disponible via sudo cat /var/lib/jenkins/secrets/initialAdminPassword')
        say('Mot de passe non affiché dans les logs et aucun compte existant modifié.')
    say('Module terminé ; contrôler TLS, listeners et services.', '✓')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyboardInterrupt, FileNotFoundError) as exc:
        say(str(exc) or 'Interrompu', '✗')
        sys.exit(1)
