#!/usr/bin/env python3
"""AUTOKOM Jenkins post-install configurator (existing secured instances).

No change to an existing security realm / authorization strategy. Plugin install
uses authenticated Jenkins CLI; secrets never appear in command-line arguments.
"""
import argparse
from contextlib import contextmanager, ExitStack
import getpass
import tempfile
from urllib.parse import urlsplit
import os
import secrets
import time
import pwd
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from menu import pick, ask

ROOT = Path(__file__).resolve().parents[2]
JENKINS_HOME = Path('/var/lib/jenkins')
JENKINS_URL = 'http://127.0.0.1:8080/'
CLI_JAR = Path('/opt/autokom/tools/jenkins-cli.jar')
SENSITIVE = Path('/etc/autokom/secrets')
PROFILES = ROOT / 'config/jenkins'
ANSI = sys.stdout.isatty() and not os.environ.get('NO_COLOR')


def output(symbol, message):
    color = {'✓': '32', '+': '36', '⚠': '33', '✗': '31', '›': '36'}.get(symbol, '0')
    prefix = f'\033[{color}m{symbol}\033[0m' if ANSI else symbol
    print(f'  {prefix} {message}', flush=True)


def command(argv, *, stdin=None, timeout=180, acceptable=(0,), status=''):
    if not status or not sys.stdout.isatty() or os.environ.get('AUTOKOM_LIVE') == '0':
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
    else:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        start = time.monotonic()
        shown = False
        try:
            while True:
                try:
                    out, err = proc.communicate(input=stdin, timeout=0.3)
                    break
                except subprocess.TimeoutExpired:
                    stdin = None  # communicate may resume without re-sending input
                    if time.monotonic() - start < 0.6:
                        continue
                    if not shown:
                        sys.stdout.write('\n\n\n')
                        shown = True
                    elapsed = int(time.monotonic() - start)
                    lines = [f'  \033[32m⠸ {status}\033[0m',
                             '  \033[32m↳ Opération Jenkins en cours (résultat après exécution)\033[0m',
                             f'  \033[32m↳ {elapsed//60:02d}:{elapsed%60:02d} écoulées\033[0m']
                    sys.stdout.write('\033[3A')
                    for line in lines:
                        sys.stdout.write('\r\033[2K' + line + '\n')
                    sys.stdout.flush()
            result = subprocess.CompletedProcess(argv, proc.returncode, out, err)
        except Exception:
            proc.kill()
            proc.communicate()
            raise
        finally:
            if shown:
                sys.stdout.write('\033[3A' + ('\r\033[2K\n' * 3) + '\033[3A')
                sys.stdout.flush()
    if result.returncode not in acceptable:
        # CLI stdout may be useful; do not print command line, which can contain auth paths.
        raise RuntimeError(f'Commande échouée ({argv[0]}), code {result.returncode}: {(result.stderr or result.stdout)[-900:]}')
    return result.stdout


def jenkins_state():
    """secure | wizard | unsafe (read-only; never infer readiness from config.xml).

    Jenkins creates a temporary 'admin' AND a secure config.xml during wizard.
    The official completed-setup marker is the authoritative discriminator.
    """
    marker = JENKINS_HOME / 'jenkins.install.UpgradeWizard.state'
    initial = JENKINS_HOME / 'secrets/initialAdminPassword'
    if marker.is_file():
        return 'secure' if jenkins_xml_secure() else 'unsafe'
    # Existing instances using a custom provisioning mechanism may be secure
    # without the standard wizard marker. Do not assume they need a reset.
    if not initial.is_file():
        return 'secure' if jenkins_xml_secure() else 'unsafe'
    # New wizard: the only expected account is temporary 'admin'.
    users = active_users()
    if len(users) <= 1 and all(u.name == 'admin' or u.name.startswith('admin_') for u in users):
        return 'wizard'
    return 'unsafe'


def trusted_web_url(override=''):
    """Provide a clickable URL only when explicit HTTPS or trusted Nginx discovery.

    Never synthesize a public URL from a private network interface or accept
    arbitrary Jenkins-configured redirects for credential guidance.
    """
    if override:
        parts = urlsplit(override)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or
                parts.password or parts.port not in (None, 443) or parts.query or
                parts.fragment or parts.path not in ('', '/')):
            raise ValueError('--public-url attend une URL HTTPS du type https://jenkins.exemple.org')
        return 'https://' + parts.hostname.lower() + '/'
    try:
        from nginx_discovery import inventory, jenkins_domains
        domains = jenkins_domains(inventory())
        if domains:
            return 'https://' + domains[0] + '/'
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError):
        pass
    return ''


def first_run_guidance(web_url, *, apply, timeout=900):
    if web_url:
        output('›', 'Premier démarrage Jenkins : ' + web_url)
    else:
        output('⚠', 'Aucun domaine Jenkins HTTPS validé. Utiliser un tunnel SSH vers 127.0.0.1:8080.')
        output('›', 'Depuis votre ordinateur : ssh -L 8080:127.0.0.1:8080 utilisateur@serveur')
        output('›', 'Puis ouvrir http://127.0.0.1:8080/')
    output('›', '1. Déverrouiller Jenkins avec le mot de passe initial :')
    output('›', '   sudo cat /var/lib/jenkins/secrets/initialAdminPassword')
    output('›', '2. Dans le wizard : « Select plugins to install » → « None » → « Install »')
    output('›', '   (AUTOKOM installera ensuite le profil de plugins choisi.)')
    output('›', '3. Créer le PREMIER administrateur et terminer l’assistant officiel.')
    if not apply:
        output('›', 'Simulation : aucun assistant attendu, aucune configuration modifiée.')
        return False
    output('›', 'AUTOKOM surveille la fin de la configuration, puis reprend automatiquement.')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if jenkins_state() == 'secure':
            output('✓', 'Jenkins initialisé : sécurité active, accès administrateur prêt.')
            return True
        time.sleep(2)
    raise RuntimeError('Assistant Jenkins non terminé dans le délai. Relancer --apply après création du compte ; aucun compte n’a été remplacé.')


def read_masked(label):
    """Get a secret from /dev/tty without echo or exposing it in process args."""
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        os.close(fd)
    except OSError as error:
        raise RuntimeError('Terminal interactif requis pour saisir un token masqué ; utiliser --auth-file en automatisation') from error
    try:
        value = getpass.getpass('  ' + label + ': ').strip()
    except (EOFError, OSError) as error:
        raise RuntimeError('Impossible de saisir un secret depuis le terminal ; utiliser --auth-file') from error
    if not value or '\n' in value or '\r' in value:
        raise ValueError('Secret vide ou invalide')
    return value


@contextmanager
def transient_credentials(username, secret, label='auth'):
    """Create a root-private ephemeral file compatible with Jenkins CLI -auth @file."""
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,100}', username):
        raise ValueError('Identifiant Jenkins invalide')
    if not secret or '\n' in secret or '\r' in secret or '\x00' in secret:
        raise ValueError('Token/mot de passe invalide')
    with tempfile.TemporaryDirectory(prefix='autokom-jenkins-', dir='/run') as directory:
        path = Path(directory) / label
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(username + ':' + secret + '\n' if label == 'auth' else secret + '\n')
        yield str(path)
    # All ephemeral credentials are removed by TemporaryDirectory, success or failure.


@contextmanager
def authorization_context(args, web_url, *, apply):
    """Acquire auth only after first-run wizard. Reuse --auth-file noninteractively."""
    if not apply:
        yield ''
        return
    if args.auth_file:
        yield str(secure_file(args.auth_file))
        return
    if not args.interactive:
        raise RuntimeError('Mode non interactif : fournir --auth-file avec username:API_TOKEN')
    if not web_url:
        raise RuntimeError('Aucun accès HTTPS Jenkins détecté : fournir --public-url ou configurer Nginx avant la création du token')
    output('›', 'Créer un API token : ' + web_url + 'me/security/')
    output('›', 'Dans Jenkins : Security → API Token → Add new Token → Generate → Copy.')
    user = ask('Identifiant administrateur Jenkins', hint='Saisir le nom du compte Jenkins déjà créé et connecté.')
    token = read_masked('Coller le token API (saisie masquée)')
    with transient_credentials(user, token) as path:
        token = None
        yield path


def prepare_extra_admin(args, stack):
    """Collect a new administrator password privately only when specifically requested."""
    if not args.admin_user or args.admin_password_file:
        return
    if not args.interactive:
        raise ValueError('--admin-password-file requis pour --admin-user sans terminal interactif')
    password = read_masked('Mot de passe du nouvel administrateur (16 caractères minimum)')
    again = read_masked('Confirmer ce mot de passe')
    if password != again or len(password) < 16:
        raise ValueError('Les mots de passe diffèrent ou sont trop courts (16 caractères minimum)')
    context = stack.enter_context(transient_credentials('unused', password, 'admin-password'))
    args.admin_password_file = context
    password = again = None


def plugin_ids(profile, include_multibranch_scan=False):
    files = ['plugins-baseline.txt'] if profile != 'minimal' else []
    if profile == 'devsecops':
        files += ['plugins-devsecops.txt']
    plugins = []
    for filename in files:
        for line in (PROFILES / filename).read_text(encoding='utf-8').splitlines():
            plugin = line.split('#', 1)[0].strip()
            if plugin and plugin not in plugins:
                if not re.fullmatch(r'[a-z0-9][a-z0-9-]*(?::[\w.\-]+)?', plugin):
                    raise ValueError(f'Nom de plugin invalide: {plugin}')
                plugins.append(plugin)
    if include_multibranch_scan:
        plugins.append('multibranch-scan-webhook-trigger')
    return plugins


def local_plugin_ids(path=JENKINS_HOME / 'plugins'):
    if not path.exists():
        return set()
    return {p.stem for p in path.iterdir() if p.is_file() and p.suffix in ('.jpi', '.hpi') and not (p.with_name(p.name + '.disabled')).exists()}


def active_users():
    users = JENKINS_HOME / 'users'
    return [p for p in users.iterdir() if p.is_dir()] if users.exists() else []


def jenkins_xml_secure():
    cfg = JENKINS_HOME / 'config.xml'
    if not cfg.exists():
        return False
    try:
        root = ET.parse(cfg).getroot()
    except ET.ParseError:
        return False
    realm = root.find('securityRealm')
    auth = root.find('authorizationStrategy')
    if root.findtext('useSecurity') == 'false':
        return False
    return bool(realm is not None and auth is not None and
                'None' not in realm.attrib.get('class', '') and
                'Unsecured' not in auth.attrib.get('class', ''))


def secure_file(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        raise ValueError(f'Fichier absent ou lien symbolique interdit : {p}')
    st = p.stat()
    if st.st_mode & 0o077:
        raise ValueError(f'Permissions trop ouvertes sur {p} : exiger chmod 600')
    if st.st_uid != 0:
        raise ValueError(f'Fichier sensible non propriétaire root : {p}')
    return p


def cli(auth_file, subcommand, *, stdin=None, timeout=300, status=""):
    return command(['java','-jar', str(CLI_JAR),'-s', JENKINS_URL, '-webSocket',
                    '-auth', f'@{auth_file}', *subcommand], stdin=stdin, timeout=timeout, status=status)


def ensure_cli_jar(apply):
    if CLI_JAR.exists():
        return
    if not apply:
        output('+', f'Jenkins CLI à récupérer : {CLI_JAR}')
        return
    CLI_JAR.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    req = urllib.request.Request(JENKINS_URL + 'jnlpJars/jenkins-cli.jar', headers={'User-Agent': 'Autokom/jenkins-bootstrap'})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024 or not data.startswith(b'PK'):
        raise RuntimeError('Jenkins CLI jar invalide ou trop grand')
    temp = CLI_JAR.with_suffix('.tmp')
    temp.write_bytes(data)
    temp.chmod(0o644)
    temp.replace(CLI_JAR)


def write_admin_script():
    return (ROOT / 'config/jenkins/provision-admin.groovy').read_text(encoding='utf-8')


def handle_admin(auth_file, username, password_file, apply):
    if not username:
        return
    if not re.fullmatch('[a-zA-Z][a-zA-Z0-9_.-]{2,39}', username):
        raise ValueError('Identifiant admin invalide (3 à 40 caractères)')
    secure_file(password_file)
    output('›', f'Nouvel administrateur demandé : {username} (compte existant jamais remplacé)')
    if not apply:
        return
    secret_location = JENKINS_HOME / ('.autokom-admin-' + secrets.token_hex(12))
    # Secret is read by the Jenkins process via the local Groovy script.
    # Existing admin credentials authorize the action over Jenkins CLI.
    # Atomic creation, exclusive; prevents overwriting a concurrent operation.
    password = Path(password_file).read_text(encoding='utf-8').strip()
    if len(password) < 16 or '\n' in password or '\r' in password:
        raise ValueError('Mot de passe administrateur requis : 16 caractères minimum, une seule ligne')
    try:
        fd = os.open(secret_location, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        jenkins_user = pwd.getpwnam('jenkins')
        os.fchown(fd, jenkins_user.pw_uid, jenkins_user.pw_gid)
        with os.fdopen(fd, 'w', encoding='utf-8') as fp:
            fp.write(username + '\n' + password + '\n')
        script = write_admin_script().replace('__AUTOKOM_ADMIN_FILE__', str(secret_location))
        response = cli(auth_file, ['groovy', '='], stdin=script, timeout=120)
        if 'AUTOKOM_ADMIN_CREATED' in response:
            output('✓', f'Administrateur {username} créé et autorisé')
        elif 'AUTOKOM_ADMIN_ALREADY_EXISTS' in response:
            output('✓', f'Compte {username} déjà présent, préservé')
        else:
            raise RuntimeError('Jenkins n’a pas confirmé la création du compte : vérifier son journal')
    finally:
        secret_location.unlink(missing_ok=True)


def harden(auth_file, apply, controller_zero=False, disable_agents=False):
    text = (ROOT/'config/jenkins/harden-existing.groovy').read_text(encoding='utf-8')
    text = text.replace('__AUTOKOM_CONTROLLER_ZERO__', 'true' if controller_zero else 'false')
    text = text.replace('__AUTOKOM_DISABLE_AGENTS__', 'true' if disable_agents else 'false')
    output('›', 'Politique : CSRF, inscription publique interdite, droits existants préservés')
    if not apply:
        return
    result = cli(auth_file, ['groovy', '='], stdin=text, timeout=120)
    if 'AUTOKOM_HARDENED' not in result:
        raise RuntimeError('Durcissement Jenkins non confirmé; voir journaux du service')
    output('✓', 'Contrôles Jenkins appliqués (compte et autorisations existants inchangés)')


def configure(args):
    if os.geteuid() != 0:
        raise RuntimeError('Exécuter en root/sudo')
    if subprocess.run(['systemctl','is-active','--quiet','jenkins']).returncode != 0:
        raise RuntimeError('Jenkins doit être démarré pour l’étape post-installation')
    apply = args.mode == 'apply'
    state = jenkins_state()
    web_url = trusted_web_url(args.public_url)
    output('›', 'État Jenkins : ' + {'secure': 'déjà configuré', 'wizard': 'nouveau (assistant initial)', 'unsafe': 'sécurité incertaine'}[state])
    if state == 'unsafe':
        raise RuntimeError('Sécurité Jenkins absente ou incohérente : aucune modification automatique autorisée')
    if state == 'wizard':
        if not first_run_guidance(web_url, apply=apply, timeout=args.wizard_timeout):
            output('✓', 'Simulation Jenkins neuf terminée (assistant à réaliser avant application).')
            return
    plugins = plugin_ids(args.profile, args.multibranch_scan)
    present = local_plugin_ids()
    missing = [p for p in plugins if p.split(':',1)[0] not in present]
    output('›', f'Jenkins détecté : {len(present)} plugins sur disque')
    output('✓', f'Profil {args.profile} : {len(plugins) - len(missing)} plugins déjà présents, {len(missing)} à installer')
    if args.multibranch_scan:
        output('⚠', 'Multibranch Scan Webhook Trigger comporte un avertissement de sécurité; option explicite uniquement')
    changes = bool(missing or args.admin_user or args.harden)
    if apply and not args.confirm_restart and changes:
        raise RuntimeError('Option --confirm-restart requise AVANT toute modification')
    if args.controller_zero and not args.harden:
        raise ValueError('--controller-zero exige --harden')
    if args.disable_inbound_agents and not args.harden:
        raise ValueError('--disable-inbound-agents exige --harden')
    if args.admin_user and not (args.admin_password_file or args.interactive):
        raise ValueError('--admin-password-file requis avec --admin-user en mode non interactif')
    if not changes:
        output('✓', 'Aucune modification nécessaire : profils déjà présents et sécurité inchangée.')
        return
    if not apply:
        ensure_cli_jar(False)
        if missing:
            output('+', 'Plugins à installer : ' + ', '.join(missing))
        if args.admin_user:
            output('+', 'Nouvel administrateur demandé : ' + args.admin_user)
        if args.harden:
            output('+', 'Contrôles de sécurité Jenkins prévus')
        output('✓', 'Simulation : aucun plugin, compte ou service modifié')
        return
    with ExitStack() as stack:
        auth_file = stack.enter_context(authorization_context(args, web_url, apply=True))
        prepare_extra_admin(args, stack)
        if args.admin_user:
            secure_file(args.admin_password_file)
            password = Path(args.admin_password_file).read_text(encoding='utf-8').strip()
            if len(password) < 16 or '\n' in password or '\r' in password:
                raise ValueError('Mot de passe nouvel administrateur : 16 caractères minimum')
            password = None
        ensure_cli_jar(True)
        # Prove Overall/Administer using the newly provided token BEFORE any mutation.
        result = cli(auth_file, ['groovy', '='], stdin='import jenkins.model.Jenkins; Jenkins.get().checkPermission(Jenkins.ADMINISTER); println("AUTOKOM_ADMIN_AUTHORIZED")', timeout=45)
        if 'AUTOKOM_ADMIN_AUTHORIZED' not in result:
            raise RuntimeError('Token Jenkins invalide ou permissions Overall/Administer insuffisantes')
        if args.admin_user or args.harden:
            precheck = '''import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
def j = Jenkins.get()
'''
            if args.admin_user:
                precheck += '''if (!(j.securityRealm instanceof HudsonPrivateSecurityRealm) || j.authorizationStrategy.class.name != 'hudson.security.GlobalMatrixAuthorizationStrategy') throw new IllegalStateException('AUTOKOM: creation admin requiert realm local et stratégie Matrix globale')
'''
            if args.harden:
                precheck += '''if (j.securityRealm instanceof HudsonPrivateSecurityRealm && j.securityRealm.allowsSignup()) throw new IllegalStateException('AUTOKOM: inscription publique active. Désactiver manuellement avant modification')
'''
            precheck += 'println("AUTOKOM_PRECHECK_OK")\n'
            if 'AUTOKOM_PRECHECK_OK' not in cli(auth_file, ['groovy', '='], stdin=precheck, timeout=60):
                raise RuntimeError('Pré-contrôles Jenkins non confirmés')
        if missing:
            output('+', 'Plugins à installer : ' + ', '.join(missing))
            cli(auth_file, ['install-plugin', *missing], timeout=1800, status='Installation des plugins Jenkins')
            output('✓', f'Téléchargement de {len(missing)} plugins demandé à Jenkins')
        if args.admin_user:
            handle_admin(auth_file, args.admin_user, args.admin_password_file, True)
        if args.harden:
            harden(auth_file, True, controller_zero=args.controller_zero, disable_agents=args.disable_inbound_agents)
        output('›', 'Redémarrage contrôlé : Jenkins attend la fin des builds en cours (safe-restart)')
        cli(auth_file, ['safe-restart'], timeout=60, status='Redémarrage sécurisé Jenkins')
        output('✓', 'safe-restart accepté ; vérifier la reprise et les plugins après les builds')

def parse(argv=None):
    p = argparse.ArgumentParser(description='AUTOKOM : configuration initiale de Jenkins existant')
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', dest='mode', action='store_const', const='check')
    modes.add_argument('--apply', dest='mode', action='store_const', const='apply')
    p.add_argument('--profile', choices=['minimal', 'baseline', 'devsecops'], default='devsecops')
    p.add_argument('--interactive', action='store_true', help='Menus avec flèches et chiffres 1–9')
    p.add_argument('--auth-file', default='', help='Option automatisation : fichier root 0600 avec username:API_TOKEN')
    p.add_argument('--public-url', default='', help='URL HTTPS Jenkins pour la création du token, si Nginx non détectable')
    p.add_argument('--wizard-timeout', type=int, default=900, help='Attente max du wizard initial en secondes (défaut 900)')
    p.add_argument('--admin-user', default='', help='Nouvel admin à créer si absent (pas de reset)')
    p.add_argument('--admin-password-file', default='', help='Mot de passe du nouvel admin en mode 0600')
    p.add_argument('--harden', action='store_true', help='Durcir la sécurité sans changer les droits existants')
    p.add_argument('--disable-inbound-agents', action='store_true', help='Désactiver le port TCP des agents explicitement')
    p.add_argument('--controller-zero', action='store_true', help='Désactiver explicitement les builds sur le contrôleur')
    p.add_argument('--confirm-restart', action='store_true', help='Autoriser le safe-restart après les changements')
    p.add_argument('--multibranch-scan', action='store_true', help='Plugin optionnel avec avertissement de sécurité')
    return p.parse_args(argv)


def interactive_configure(args):
    choice = pick('Jenkins — configuration AUTOKOM',
                  ['Recommandée : plugins DevSecOps + sécurité, préserver comptes et agents',
                   'Personnaliser les composants'],
                  hint='Option 1 : configuration sécurisée en limitant les interactions et sans remplacer les administrateurs.')
    if choice == 1:
        args.profile = 'devsecops'
        args.harden = True
        args.admin_user = ''
        args.controller_zero = False
        args.disable_inbound_agents = False
        args.confirm_restart = args.mode == 'apply'  # Consentement donné par la sélection de ce profil.
        output('✓', 'Profil recommandé sélectionné : comptes et agents existants préservés.')
        return args
    i = pick('Jenkins — profil de plugins',
             ['DevSecOps complet', 'Base suggérée AUTOKOM', 'Aucun nouveau plugin'],
             hint='Les plugins déjà installés ne sont ni effacés ni rétrogradés.')
    args.profile = ('devsecops','baseline','minimal')[i-1]
    # The initial setup wizard creates the FIRST admin. Do not ask for a
    # second administrator on a controller not yet initialized.
    if jenkins_state() != 'wizard':
        i = pick('Jenkins — administrateurs',
                 ['Conserver les administrateurs existants', 'Créer un administrateur supplémentaire'],
                 hint='La création additionnelle nécessite le realm local et la stratégie Matrix globale.')
        if i == 2:
            args.admin_user = ask('Identifiant du nouvel administrateur',
                                  hint='Le compte existant ne sera pas réinitialisé.')
    i = pick('Jenkins — contrôles de sécurité',
             ['Vérifier et renforcer la sécurité', 'Ne rien modifier aux contrôles'],
             hint='Aucun changement du realm ni des permissions des utilisateurs.')
    args.harden = i == 1
    if args.harden:
        i = pick('Agents Jenkins : port TCP',
                 ['Conserver la configuration existante', 'Désactiver les agents TCP entrants'],
                 hint='Choix 2 uniquement si aucun agent entrant ne dépend de ce port.')
        args.disable_inbound_agents = i == 2
        i = pick('Exécution sur le contrôleur Jenkins',
                 ['Conserver les exécuteurs actuels', 'Désactiver les builds sur le contrôleur'],
                 hint='Choix 2 nécessite des agents de build disponibles.')
        args.controller_zero = i == 2
    if args.mode == 'apply':
        i = pick('Redémarrage sécurisé Jenkins',
                 ['Autoriser safe-restart après application', 'Annuler avant toute modification'],
                 hint='safe-restart attend la fin des builds en cours.')
        if i == 2:
            raise RuntimeError('Opération annulée avant modification')
        args.confirm_restart = True
    return args


def main():
    args = parse()
    if args.wizard_timeout < 1:
        raise ValueError('--wizard-timeout doit être positif')
    if args.interactive:
        args = interactive_configure(args)
    print('\nAUTOKOM · Jenkins First-Run / Post-Install', flush=True)
    output('›', 'Mode : ' + ('Simulation' if args.mode == 'check' else 'Application'))
    configure(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, urllib.error.URLError, KeyboardInterrupt) as error:
        output('✗', str(error))
        sys.exit(1)
