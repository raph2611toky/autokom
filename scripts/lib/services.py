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
from nginx_discovery import inventory as nginx_inventory, jenkins_domains, domain_plan
from firewall import secure_firewall, ufw_active, docker_bridges
from tool_banner import render as autokom_banner, first_install, section

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
    """Accept comma, semicolon or whitespace; never hyphen/underscore as separator."""
    if not value or not value.strip():
        return []
    result = []
    for token in re.split(r'[,;\s]+', value.strip()):
        if not token:
            continue
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError as exc:
            raise ValueError(
                f'CIDR invalide : {token!r}. Séparer les réseaux par virgule, point-virgule ou espace. '
                'Exemple : 172.18.0.0/16, 172.19.0.0/16'
            ) from exc
        if network.prefixlen == 0:
            raise ValueError('Réseaux universels 0.0.0.0/0 et ::/0 interdits pour un compte applicatif')
        item = str(network)
        if item not in result:
            result.append(item)
    return result


def check_user_input(options):
    options.domain = valid_domain(options.domain) if options.domain else ''
    options.allow = cidrs(options.allow) if isinstance(options.allow, str) else options.allow
    # Backward compatibility with the old confusing CLI option --bind private.
    if options.bind == 'private':
        options.bind = 'all'
    if options.db == 'oracle' and options.bind != 'local':
        raise ValueError('Oracle Free : le module conserve uniquement une écoute locale')
    if options.db == 'mysql' and any(ipaddress.ip_network(c).version == 6 and
                                     ipaddress.ip_network(c).prefixlen != 128 for c in options.allow):
        raise ValueError('MySQL : sous-réseaux IPv6 non unitaires non pris en charge')
    if options.tls == 'existing' and options.domain and domain_plan(options.domain, nginx_inventory()) != 'existing':
        raise ValueError('--tls existing exige un domaine Nginx et un certificat valides')
    if options.tls == 'provided' and (not options.cert or not options.key):
        raise ValueError('Certificat ET clé privée requis pour --tls provided')
    if options.tls == 'certbot' and options.domain and not options.email:
        raise ValueError('E-mail Let’s Encrypt requis seulement pour un nouveau domaine')
    # 0.0.0.0 is an address to LISTEN on, NOT a client authorization.
    # Actual exposure is checked and denied by secure_firewall() before apply.
    return options


def interactive(options):
    say('Installation interactive — ↑/↓, Entrée ou chiffre 1–9')
    if options.db is None:
        idx = pick('Base de données',
                   ['PostgreSQL', 'MySQL', 'Oracle Database Free (Docker)', 'Aucune'],
                   hint='Les bases existantes seront détectées et conservées.')
        options.db = ('postgresql','mysql','oracle','none')[idx - 1]
    if options.db != 'none' and options.bind is None:
        choices = ['Locale uniquement : 127.0.0.1',
                   'Interfaces IPv4 : 0.0.0.0 (ports WAN bloqués)']
        idx = pick('Adresse d’écoute de la base', choices,
                   default=1, hint='0.0.0.0 = écoute, pas accès libre. Pare-feu obligatoire.')
        options.bind = ('local','all')[idx - 1]
    if options.db in ('mysql','postgresql') and not options.create_app_db:
        idx = pick('Créer une base AUTOKOM et son utilisateur ?',
                   ['Non : conserver les comptes et bases existants',
                    'Oui : créer autokom_apps et autokom_app'],
                   hint='Non n’altère aucune permission SQL existante.')
        options.create_app_db = idx == 2
    if options.create_app_db and options.bind == 'all' and not options.allow:
        options.allow = ask('Réseaux clients SQL autorisés (CIDR)', '',
                            hint='Séparer par virgule, ; ou espace. Ex : 172.18.0.0/16, 172.19.0.0/16. Vide = compte localhost seul.')
        # Validate early, avoiding an error after the Jenkins questions.
        cidrs(options.allow)
    if options.docker_access is None:
        idx = pick('Jenkins : accès au démon Docker',
                   ['Non : isolation maximale', 'Oui : groupe docker (root-équivalent)'],
                   hint='Le groupe docker donne des privilèges administrateur au service Jenkins.')
        options.docker_access = idx == 2
        if options.docker_access:
            options.docker_access = ask('Pour confirmer saisir DOCKER-ROOT',
                                        hint='Accès sensible au socket Docker du serveur') == 'DOCKER-ROOT'
    if options.domain is None:
        sites = nginx_inventory()
        found = jenkins_domains(sites)
        if found:
            # Offer to reuse an actual Jenkins proxy instead of recreating it.
            found = found[:7]
            choices = [f'Réutiliser {name} (Nginx + TLS valides)' for name in found]
            choices += ['Définir un nouveau domaine', 'Sans domaine (tunnel SSH)']
            idx = pick('Jenkins — domaine HTTPS', choices, default=1,
                       hint='Détection Nginx active + certificat, sans réécriture.')
            if idx <= len(found):
                options.domain = found[idx - 1]
                options.tls = 'existing'
            elif idx == len(found) + 1:
                options.domain = ask('Nouveau domaine Jenkins',
                                     hint='Enregistrement DNS A vers le serveur requis pour Certbot')
            else:
                options.domain = ''
        else:
            idx = pick('Jenkins — reverse proxy',
                       ['Configurer un domaine', 'Sans domaine (tunnel SSH)'],
                       hint='Si le domaine existe déjà, AUTOKOM refuse de l’écraser.')
            options.domain = ask('Domaine Jenkins',
                                 hint='Exemple : jenkins.example.com') if idx == 1 else ''
    if options.domain:
        current = domain_plan(valid_domain(options.domain), nginx_inventory())
        if current == 'existing':
            options.tls = 'existing'
            say(f'{options.domain} : Nginx et TLS existants validés ; Certbot ignoré', '✓')
        elif options.tls is None:
            idx = pick('HTTPS du nouveau domaine',
                       ['Certbot / Let’s Encrypt', 'Certificat déjà disponible', 'HTTP uniquement'],
                       hint='Certbot uniquement si aucun certificat valide n’est installé.')
            options.tls = ('certbot','provided','none')[idx - 1]
    if options.domain and options.tls == 'certbot' and not options.email:
        options.email = ask('Adresse e-mail pour Let’s Encrypt',
                            hint='Requise pour un nouveau certificat ACME')
    if options.domain and options.tls == 'provided':
        options.cert = ask('Chemin du certificat fullchain.pem')
        options.key = ask('Chemin de la clé privée .pem')
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
        sites = nginx_inventory()
        state = domain_plan(options.domain, sites)
        if state == 'existing':
            options.tls = 'existing'
            say(f'Jenkins HTTPS {options.domain} déjà opérationnel (Nginx + certificat) : aucune régénération.', '✓')
        else:
            # Never overwrite an inactive vhost file of the same name.
            for folder in ('/etc/nginx/sites-available', '/etc/nginx/sites-enabled'):
                if (Path(folder) / f'{options.domain}.conf').exists():
                    fail('Domaine trouvé dans les fichiers Nginx mais absent de la configuration active : vérifier manuellement')
    say(f'Jenkins : présent={shutil.which("jenkins") is not None} ; domaine={options.domain or "aucun"}')
    say(f'Base : {options.db} ; bind demandé={mysql_requested_address(options) if options.db == "mysql" else options.bind}')
    if options.create_app_db:
        say('Réseaux autorisés pour le NOUVEAU compte AUTOKOM : ' + (', '.join(options.allow) or 'localhost uniquement'))
    elif options.allow:
        say('CIDR fournis mais aucun nouveau compte SQL créé : comptes existants non modifiés', '!')
    say('Création de base applicative : ' + ('oui' if options.create_app_db else 'non (conserver l’existant)'))
    if options.db == 'mysql':
        live = mysql_live_bind() if shutil.which('mysql') else None
        if live is None:
            say('MySQL absent ou accès root via socket indisponible : état à confirmer après installation', '!')
        else:
            status = 'CONFORME' if mysql_bind_equivalent(live, mysql_requested_address(options)) else 'CHANGEMENT REQUIS'
            say(f'MySQL existant : @@GLOBAL.bind_address={live} -> {status}', '✓' if status == 'CONFORME' else '!')
            listeners = mysql_listener_status()
            say('MySQL listeners : ' + (', '.join(listeners) if listeners else 'aucun port TCP 3306 détecté'), '✓' if listeners else '!')
            if not listeners:
                say('MySQL répond au socket local mais ne semble pas écouter en TCP sur 3306', '!')
            broad = run(['mysql', '--protocol=socket', '-uroot', '-Nse',
                         "SELECT COUNT(*) FROM mysql.user WHERE Host='%'"], check=False)
            if broad.returncode == 0 and broad.stdout.strip().isdigit() and int(broad.stdout.strip()) > 0:
                say(f'MySQL : {broad.stdout.strip()} compte(s) avec Host=% déjà présents (inchangés)', '!')
            mysqlx = run(['mysql', '--protocol=socket', '-uroot', '-Nse',
                          "SHOW VARIABLES LIKE 'mysqlx_bind_address'"], check=False)
            if mysqlx.returncode == 0 and mysqlx.stdout.strip():
                say('MySQL X Plugin : ' + mysqlx.stdout.strip().replace('\t', '=') + ' (écoute indépendante à contrôler)')
    if options.db == 'postgresql' and shutil.which('psql'):
        result = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc',
                      'SHOW listen_addresses'], check=False)
        if result.returncode == 0:
            say('PostgreSQL existant : listen_addresses=' + result.stdout.strip())
    if shutil.which('jenkins'):
        listeners = run(['ss', '-ltn'], check=False).stdout
        exposed = any(re.search(r'(?:0\.0\.0\.0|\*|\[::\]):8080\s', x) for x in listeners.splitlines())
        say(f'Jenkins existant : port 8080 {"sur toutes interfaces" if exposed else "à vérifier / local"}', '!' if exposed else '✓')
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
    """Existing cluster: detect actual listener and avoid unnecessary restart."""
    base = Path('/etc/postgresql')
    clusters = sorted((d for d in base.glob('*/main') if d.is_dir()),
                      key=lambda d: tuple(int(n) for n in d.parent.name.split('.')), reverse=True)
    if not clusters:
        fail('Aucun cluster PostgreSQL main détecté : ne rien modifier')
    cfg = clusters[0]
    version = cfg.parent.name
    main_conf = cfg / 'postgresql.conf'
    hba = cfg / 'pg_hba.conf'
    if not main_conf.is_file() or not hba.is_file():
        fail('Configuration PostgreSQL incomplète ; opération annulée')

    # Refuse collisions before changing configuration.
    role_exists = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc',
                       "SELECT 1 FROM pg_roles WHERE rolname='autokom_app'"]).stdout.strip()
    if options.create_app_db and role_exists and not (SECRETS / 'postgresql-autokom-app.password').is_file():
        fail('autokom_app existe sans secret géré : compte et configuration préservés')

    requested = '127.0.0.1' if options.bind == 'local' else '0.0.0.0'
    observed = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc',
                    'SHOW listen_addresses']).stdout.strip()
    accepted = (observed == requested or (requested == '127.0.0.1' and observed in ('localhost', '127.0.0.1,::1')))
    if accepted:
        say(f'PostgreSQL déjà conforme : listen_addresses={observed}, aucun redémarrage', '✓')
    else:
        say(f'PostgreSQL : écoute actuelle={observed} ; demandée={requested}', '!')
        require_restart_approval(f'postgresql@{version}-main', options)
        content = main_conf.read_text()
        # Avoid overriding a possibly complex configuration with multiple active assignments.
        replacements = list(re.finditer(r'^[ \t]*listen_addresses[ \t]*=', content, flags=re.M))
        if len(replacements) > 1:
            fail('PostgreSQL : plusieurs listen_addresses actifs, intervention manuelle nécessaire')
        pattern = r'^[ \t]*#?[ \t]*listen_addresses[ \t]*=.*$'
        update = f"listen_addresses = '{requested}'  {MANAGED}"
        found = list(re.finditer(pattern, content, flags=re.M))
        if found:
            # Replace the final active line or final commented line, preserving unrelated settings.
            selected = next((m for m in reversed(found) if not m.group().lstrip().startswith('#')), found[-1])
            new_conf = content[:selected.start()] + update + content[selected.end():]
        else:
            new_conf = content.rstrip() + '\n' + update + '\n'
        previous = content
        if new_conf != previous:
            backup(main_conf)
            atomic_write(main_conf, new_conf)
        try:
            run(['pg_ctlcluster', version, 'main', 'restart'])
            actual = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc',
                          'SHOW listen_addresses']).stdout.strip()
            if actual != requested and not (requested == '127.0.0.1' and actual in ('localhost', '127.0.0.1,::1')):
                fail(f'PostgreSQL : écoute après redémarrage incorrecte ({actual})')
        except Exception:
            atomic_write(main_conf, previous)
            run(['pg_ctlcluster', version, 'main', 'restart'], check=False)
            raise
        say(f'PostgreSQL : listen_addresses={requested}, rechargement contrôlé', '✓')

    if not options.create_app_db:
        say('PostgreSQL : bases et utilisateurs existants conservés', '✓')
        return

    start = '# BEGIN AUTOKOM ACCESS'
    end = '# END AUTOKOM ACCESS'
    networks = sorted(set(['127.0.0.1/32', '::1/128'] + options.allow))
    lines = [start, '# Autokom app only; autres rôles inchangés']
    lines.extend(f'host autokom_apps autokom_app {net} scram-sha-256' for net in networks)
    lines.extend(['host all autokom_app 0.0.0.0/0 reject',
                  'host all autokom_app ::/0 reject', end])
    existing_hba = hba.read_text()
    remaining = re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\n?', '',
                       existing_hba, flags=re.S)
    desired_hba = '\n'.join(lines) + '\n' + remaining
    if desired_hba != existing_hba:
        backup(hba)
        atomic_write(hba, desired_hba)
        run(['pg_ctlcluster', version, 'main', 'reload'])

    if not role_exists:
        password = write_secret('postgresql-autokom-app.password')
        # Password generated server-side; never echoed or logged.
        run(['runuser', '-u', 'postgres', '--', 'psql', '-v', 'ON_ERROR_STOP=1'],
            input_text=f"CREATE ROLE autokom_app LOGIN PASSWORD '{password}';\n")
    exists = run(['runuser', '-u', 'postgres', '--', 'psql', '-Atqc',
                  "SELECT 1 FROM pg_database WHERE datname='autokom_apps'"]).stdout.strip()
    if not exists:
        run(['runuser', '-u', 'postgres', '--', 'createdb', '-O', 'autokom_app', 'autokom_apps'])
    say(f'PostgreSQL : cluster {version}/main, rôle et accès applicatifs vérifiés', '✓')

def mysql_requested_address(options):
    return '127.0.0.1' if options.bind == 'local' else '0.0.0.0'


def mysql_bind_equivalent(observed, requested):
    """Exact effective bind: * is broader than 0.0.0.0 (also IPv6)."""
    observed = observed.strip().strip('\"')
    return observed == requested


def mysql_live_bind():
    result = run(['mysql', '--protocol=socket', '-uroot', '-Nse',
                  'SELECT @@GLOBAL.bind_address'], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def mysql_listener_status():
    """Inspect actual 3306 TCP listeners, independent of MySQL option files."""
    output = run(['ss', '-ltn'], check=False).stdout
    return [line.split()[3] for line in output.splitlines()
            if re.search(r':3306\s', line) and len(line.split()) >= 4]


def mysql_hosts_for_options(options):
    """Listen address never grants clients. Hosts are an independent allowlist."""
    hosts = {'localhost'}
    for subnet in options.allow:
        net = ipaddress.ip_network(subnet, strict=False)
        if net.version == 4:
            if net.prefixlen == 0:
                hosts.add('%')
            elif net.prefixlen == 32:
                hosts.add(str(net.network_address))
            else:
                hosts.add(f'{net.network_address}/{net.netmask}')
        elif net.prefixlen == 128:
            hosts.add(str(net.network_address))
        else:
            raise ValueError('MySQL : plage IPv6 > /128 non prise en charge pour les comptes')
    return sorted(hosts)


def require_restart_approval(service, options):
    """Existing production services need explicit acknowledgement of interruption."""
    if run(['systemctl', 'is-active', '--quiet', service], check=False).returncode != 0:
        return
    if options.confirm_restart:
        return
    if options.non_interactive:
        fail(f'{service} nécessite un redémarrage : utiliser --confirm-restart après maintenance planifiée')
    if ask(f'{service} ACTIF : interruption possible, saisir REDEMARRER pour confirmer') != 'REDEMARRER':
        fail(f'Redémarrage {service} refusé ; aucune configuration appliquée')

def mysql_config(options):
    """Preserve running MySQL when effective listener already meets the request."""
    if run(['mysql', '--protocol=socket', '-uroot', '-Nse', 'SELECT 1'], check=False).returncode:
        fail('MySQL root via socket indisponible : accès administrateur à configurer sans modifier le service')

    # Check for conflicting AUTOKOM credentials BEFORE any service modifications.
    existing = run(['mysql', '--protocol=socket', '-uroot', '-Nse',
                    "SELECT COUNT(*) FROM mysql.user WHERE user='autokom_app'"]).stdout.strip()
    if options.create_app_db and int(existing or '0') > 0 and not (SECRETS / 'mysql-autokom-app.password').exists():
        fail('Compte autokom_app existant sans secret AUTOKOM : configuration préservée')

    requested = mysql_requested_address(options)
    observed = mysql_live_bind()
    if observed is None:
        fail('Impossible de lire @@GLOBAL.bind_address : configuration préservée')
    if mysql_bind_equivalent(observed, requested):
        say(f'MySQL déjà conforme : bind effectif={observed} ; pas de redémarrage', '✓')
    else:
        say(f'MySQL : bind actuel={observed}, demandé={requested}', '!')
        require_restart_approval('mysql', options)
        # A name ordered AFTER mysqld.cnf (90-autokom.cnf can be overridden by mysqld.cnf).
        conf = Path('/etc/mysql/mysql.conf.d/zz-autokom.cnf')
        desired = f'{MANAGED}\n[mysqld]\nbind-address = {requested}\n'
        if conf.exists() and MANAGED not in conf.read_text(errors='replace'):
            fail(f'Configuration existante non AUTOKOM : {conf}')
        previous = conf.read_text() if conf.exists() else None
        if conf.exists():
            backup(conf)
        changed = atomic_write(conf, desired)
        try:
            if changed:
                run(['mysqld', '--validate-config', '--user=mysql'])
                run(['systemctl', 'restart', 'mysql'])
            if run(['systemctl', 'is-active', '--quiet', 'mysql'], check=False).returncode:
                fail('MySQL ne redémarre pas : tentative de restauration')
            effective = mysql_live_bind()
            if effective is None or not mysql_bind_equivalent(effective, requested):
                fail(f'MySQL n’écoute pas selon la configuration demandée (effectif={effective})')
        except Exception:
            if previous is None:
                conf.unlink(missing_ok=True)
            else:
                atomic_write(conf, previous)
            run(['systemctl', 'restart', 'mysql'], check=False)
            raise
        say(f'MySQL : bind configuré et vérifié ({effective})', '✓')

    listeners = mysql_listener_status()
    if not listeners:
        fail('MySQL : bind configuré mais aucun listener TCP 3306 détecté')
    if requested == '0.0.0.0' and not any(x.startswith(('0.0.0.0:', '*:')) for x in listeners):
        fail(f'MySQL : listener TCP non conforme pour 0.0.0.0 : {listeners}')

    if not options.create_app_db:
        say('MySQL : bases et comptes SQL existants conservés', '✓')
        return

    # Accounts use explicit CIDRs; binding to 0.0.0.0 never implies account@'%'.
    password = write_secret('mysql-autokom-app.password')
    hosts = mysql_hosts_for_options(options)
    stmts = ['CREATE DATABASE IF NOT EXISTS `autokom_apps` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;']
    for host in hosts:
        stmts.extend([
            f"CREATE USER IF NOT EXISTS 'autokom_app'@'{host}' IDENTIFIED BY '{password}';",
            f"GRANT ALL PRIVILEGES ON `autokom_apps`.* TO 'autokom_app'@'{host}';"
        ])
    run(['mysql', '--protocol=socket', '-uroot'], input_text='\n'.join(stmts) + '\n')
    say(f'MySQL : utilisateur autokom_app, hôtes autorisés = {", ".join(hosts)}', '✓')
    say('Secret : /etc/autokom/secrets/mysql-autokom-app.password (root uniquement)')
    if options.bind != 'local':
        say('Écoute 0.0.0.0 active : les Security Groups / pare-feu restent à restreindre.', '!')

def oracle_config(options):
    name = 'autokom-oracle'
    if run(['docker','container','inspect',name], check=False).returncode == 0:
        state = run(['docker', 'inspect', '-f', '{{.State.Running}}', name], check=False).stdout.strip()
        bindings = run(['docker', 'port', name, '1521/tcp'], check=False).stdout.strip()
        if state != 'true' or '127.0.0.1:1521' not in bindings:
            fail(f'Oracle existant non conforme (running={state}, ports={bindings}) : aucune recréation automatique')
        say('Oracle Free existant : running et port localhost:1521 conformes', '✓')
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
    """Set location for new/uninitialized Jenkins; preserve custom existing settings."""
    if not options.domain:
        return
    destination = Path('/var/lib/jenkins/init.groovy.d/90-autokom-location.groovy')
    url = ('https://' if options.tls != 'none' else 'http://') + options.domain + '/'
    xml = Path('/var/lib/jenkins/jenkins.model.JenkinsLocationConfiguration.xml')
    if xml.is_file():
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml.read_text())
            current = root.findtext('jenkinsUrl') or ''
        except ET.ParseError:
            fail('Configuration URL Jenkins illisible : préservation sans modification')
        if current.rstrip('/') == url.rstrip('/'):
            say(f'Jenkins : URL publique déjà conforme ({url})', '✓')
            return
        if current and current not in ('http://localhost:8080/', 'http://127.0.0.1:8080/'):
            say(f'Jenkins : URL existante {current} != demandée {url}, préservée (à vérifier)', '!')
            return
    groovy = f"""// {MANAGED}
import jenkins.model.JenkinsLocationConfiguration

def location = JenkinsLocationConfiguration.get()
def current = location.getUrl()
if (current == null || current.trim() == '' ||
    current == 'http://localhost:8080/' || current == 'http://127.0.0.1:8080/') {{
    location.setUrl('{url}')
    location.save()
}}
"""
    if destination.exists() and MANAGED not in destination.read_text(errors='replace'):
        fail('Script init Jenkins existant non AUTOKOM : opération refusée')
    changed = atomic_write(destination, groovy, 0o644)
    if changed:
        run(['chown', 'jenkins:jenkins', str(destination)])
        os.environ['AUTOKOM_JENKINS_URL_CHANGED'] = '1'
        say('Jenkins : URL initiale préparée pour le prochain redémarrage', '✓')

def configure_jenkins_listen(options):
    path = Path('/etc/systemd/system/jenkins.service.d/20-autokom-loopback.conf')
    content = MANAGED + '\n[Service]\nEnvironment="JENKINS_LISTEN_ADDRESS=127.0.0.1"\n'
    if path.exists() and MANAGED not in path.read_text(errors='replace'):
        fail(f'Override Jenkins d’un tiers : {path}')
    listen = run(['ss', '-ltn'], check=False).stdout
    any_port = any(re.search(r':8080\s', line) for line in listen.splitlines())
    exposed = any(re.search(r'(?:0\.0\.0\.0|\*|\[::\]):8080\s', line) for line in listen.splitlines())
    on_local = any_port and not exposed and any(re.search(r'(?:127\.0\.0\.1|\[::1\]):8080\s', line) for line in listen.splitlines())

    # Only add the override if the runtime does not already listen on loopback.
    needs_override = not on_local
    previous = path.read_text() if path.exists() else None
    config_changed = needs_override and (previous != content)
    restart_needed = config_changed or (os.environ.get('AUTOKOM_JENKINS_URL_CHANGED') == '1')
    pid = run(['systemctl', 'show', '-p', 'MainPID', '--value', 'jenkins'], check=False).stdout.strip()
    if pid.isdigit() and int(pid) > 0 and options.docker_access:
        try:
            docker_gid = grp.getgrnam('docker').gr_gid
            group_line = next(line for line in Path(f'/proc/{pid}/status').read_text().splitlines() if line.startswith('Groups:'))
            if docker_gid not in [int(n) for n in group_line.split()[1:]]:
                restart_needed = True
        except (KeyError, OSError, StopIteration):
            restart_needed = True

    if restart_needed:
        require_restart_approval('jenkins', options)
        if config_changed:
            atomic_write(path, content)
            run(['systemctl', 'daemon-reload'])
        try:
            run(['systemctl', 'restart', 'jenkins'])
            if run(['systemctl', 'is-active', '--quiet', 'jenkins'], check=False).returncode:
                fail('Jenkins ne démarre pas après reconfiguration')
        except Exception:
            if config_changed:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, previous)
                run(['systemctl', 'daemon-reload'], check=False)
                run(['systemctl', 'restart', 'jenkins'], check=False)
            raise
    else:
        say('Jenkins : écoute locale déjà conforme ; aucun redémarrage', '✓')

    listeners = run(['ss', '-ltn'], check=False).stdout
    exposed_after = any(re.search(r'(?:0\.0\.0\.0|\*|\[::\]):8080\s', line) for line in listeners.splitlines())
    if exposed_after:
        fail('Jenkins écoute toujours sur une interface publique : vérifier les overrides systemd')
    if run(['systemctl', 'is-active', '--quiet', 'jenkins'], check=False).returncode:
        fail('Jenkins inactif : vérifier journalctl -u jenkins')
    say('Jenkins : service actif, port 8080 non exposé publiquement', '✓')

def configure_proxy(options):
    if not options.domain:
        say('Jenkins sans domaine : accès localhost / tunnel SSH', '›')
        return
    domain = options.domain
    state = domain_plan(domain, nginx_inventory())
    if state == 'existing':
        say(f'{domain} : Nginx + HTTPS valides, proxy réutilisé ; aucun Certbot ni reload', '✓')
        return
    available = Path('/etc/nginx/sites-available') / f'{domain}.conf'
    enabled = Path('/etc/nginx/sites-enabled') / f'{domain}.conf'
    if available.exists() or enabled.exists() or enabled.is_symlink():
        fail(f'Fichier Nginx déjà présent pour {domain} : modification refusée')
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



def jenkins_package_installed():
    """Presence du paquet natif, sans changements et sans suppositions sur le port."""
    try:
        p = subprocess.run(['dpkg-query', '-W', '-f=${Status}', 'jenkins'],
                           capture_output=True, text=True, timeout=8, check=False)
        return ((p.returncode == 0 and p.stdout.strip() == 'install ok installed')
                or Path('/usr/bin/jenkins').is_file())
    except (OSError, subprocess.TimeoutExpired):
        # Ne pas declarer un service absent si l'inventaire est indisponible.
        raise RuntimeError('Impossible de déterminer si Jenkins est installé : dpkg-query indisponible')


def main():
    parser = argparse.ArgumentParser(description='AUTOKOM Jenkins / Database module')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_const', const='check', dest='mode')
    group.add_argument('--apply', action='store_const', const='apply', dest='mode')
    parser.add_argument('--db', choices=('postgresql','mysql','oracle','none'))
    parser.add_argument('--bind', choices=('local','private','all'))
    parser.add_argument('--allow', default='', help='Réseaux SQL pour les NOUVEAUX comptes; séparateurs virgule, point-virgule, espace')
    parser.add_argument('--unsafe-all', action='store_true', help='Obsolète : la sécurité est maintenant assurée par UFW / règles WAN')
    parser.add_argument('--create-app-db', action='store_true', help='Créer explicitement autokom_apps et son compte dédié')
    parser.add_argument('--confirm-restart', action='store_true', help='Autoriser les redémarrages de services actifs en cas de changement')
    parser.add_argument('--docker-access', choices=('yes','no'))
    parser.add_argument('--domain', default=None)
    parser.add_argument('--tls', choices=('certbot','provided','none','existing'))
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--email')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--enable-ufw', action='store_true', help='Autoriser explicitement activation UFW avec garde SSH')
    parser.add_argument('--ssh-cidr', default='', help='Adresse ou CIDR de gestion SSH exigé pour activation UFW')
    parser.add_argument('--ssh-port', type=int, default=22, help='Port SSH à préserver, défaut 22')
    parser.add_argument('--confirm-firewall', action='store_true', help='Confirmer activation initiale du pare-feu')
    args = parser.parse_args()
    args.docker_access = {'yes': True, 'no': False}.get(args.docker_access)
    if os.geteuid() != 0:
        fail('Exécuter en root/sudo')
    section('Jenkins & Bases de données', 'Découverte → configuration → validation')
    if not args.non_interactive:
        args = interactive(args)
    elif args.db is None or args.docker_access is None or args.domain is None:
        parser.error('--non-interactive exige --db, --docker-access, --domain (chaîne vide autorisée)')
    if args.domain:
        # Noninteractive callers should also reuse valid Nginx+TLS without ACME options.
        if domain_plan(valid_domain(args.domain), nginx_inventory()) == 'existing':
            args.tls = 'existing'
    args = effective_option_defaults(args)
    if args.mode == 'apply' and not ufw_active() and args.enable_ufw and not args.non_interactive and not args.confirm_firewall:
        args.confirm_firewall = (ask('Activation UFW sur SSH : saisir ACTIVER-PAREFEU') == 'ACTIVER-PAREFEU')
    print('\n  \033[1;36mAUTOKOM · Jenkins & Databases\033[0m')
    say('Mode : ' + ('Simulation' if args.mode == 'check' else 'Installation'))
    preflight(args)
    # Guard BEFORE package/systemd changes; check mode is read-only.
    secure_firewall(
        args.db, apply=(args.mode == 'apply'), activate=args.enable_ufw,
        ssh_cidr=args.ssh_cidr, ssh_port=args.ssh_port,
        confirmation=args.confirm_firewall,
        say=lambda message: say(message, '›'),
    )
    if first_install(args.mode, jenkins_package_installed()):
        autokom_banner('jenkins', initial_install=True)
    ansible_install(args)
    if args.mode == 'check':
        say('Simulation terminée. Aucune configuration Jenkins ou DB modifiée.', '✓')
        return
    os.environ['AUTOKOM_JENKINS_DOCKER'] = '1' if args.docker_access else '0'
    # Existing Jenkins may need an interruption to harden its listener.
    # Ask before writing the Groovy URL initializer.
    if run(['systemctl', 'is-active', '--quiet', 'jenkins'], check=False).returncode == 0:
        listen = run(['ss', '-ltn'], check=False).stdout
        if any(re.search(r'(?:0\.0\.0\.0|\*|\[::\]):8080\s', line) for line in listen.splitlines()):
            require_restart_approval('jenkins', args)
            args.confirm_restart = True
    configure_jenkins_url(args)
    configure_jenkins_listen(args)
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
