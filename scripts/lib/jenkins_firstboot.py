#!/usr/bin/env python3
"""One-time Jenkins admin bootstrap for an unconfigured, stock wizard only.

No Jenkins passwords or API tokens are printed or put in process arguments.
Secrets remain root:0600; Jenkins sees a transient local seed only at restart.
"""
import os
import json
import hashlib
import shutil
import urllib.request
import urllib.error
import base64
import pwd
import re
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JENKINS_HOME = Path('/var/lib/jenkins')
SECRETS = Path('/etc/autokom/secrets')
USER = 'autokom-admin'
# Exact SHA-256 of AUTOKOM's published e39c first-boot hook and c070 hook.
# No arbitrary Groovy file may be replaced by a newer AUTOKOM version.
OLDER_AUTOKOM_HOOKS = frozenset({
    'b0462bffe8cdf82bea7c1bc26abb7e509abb10393ccbb4a5d948a430d09ea5e8',
    '27d47e705f7cf44277b6c0b859d0233d848a16ba43f5cb638da10a94dc8e6789',
})
PASSWORD = SECRETS / 'jenkins-first-admin.password'
PROFILE = SECRETS / 'jenkins-first-admin.profile.json'
OLD_PASSWORD = SECRETS / 'jenkins-first-admin.password.previous-auto'
AUTH = SECRETS / 'jenkins-admin.auth'
STAGING = JENKINS_HOME / '.autokom-firstboot'
SEED = STAGING / 'admin.seed'
TOKEN = STAGING / 'admin.auth.pending'
LEGACY_PROOF = STAGING / 'legacy.auto-credential'
LEGACY_PRESERVED = STAGING / 'legacy.preserved'
SCRIPT = JENKINS_HOME / 'init.groovy.d' / '95-autokom-first-admin.groovy'
TEMPLATE = ROOT / 'config/jenkins/first-admin-bootstrap.groovy'
MATRIX_TEMPLATE = ROOT / 'config/jenkins/first-admin-matrix.groovy'
MATRIX_SCRIPT = JENKINS_HOME / 'init.groovy.d/96-autokom-first-admin-matrix.groovy'
MATRIX_PENDING = STAGING / 'matrix.pending'
MATRIX_DONE = STAGING / 'matrix.done'
ADMIN_MAIL_PROFILE = STAGING / 'admin.profile'
METRICS_PASSWORD = SECRETS / 'jenkins-metrics.password'
METRICS_AUTH = SECRETS / 'jenkins-metrics.auth'
METRICS_SEED = STAGING / 'metrics.seed'
METRICS_TOKEN = STAGING / 'metrics.auth.pending'


def _secure_file(path, owner=0):
    st = path.lstat()
    if not path.is_file() or path.is_symlink() or st.st_uid != owner or st.st_mode & 0o077:
        raise RuntimeError('AUTOKOM Jenkins: fichier de secrets non protégé ; aucune réinitialisation effectuée')


def _write_new(path, data, *, owner=0, group=0, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    try:
        os.fchown(fd, owner, group)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _private_root_directory():
    if SECRETS.is_symlink():
        raise RuntimeError('AUTOKOM Jenkins: répertoire des secrets en lien symbolique')
    SECRETS.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = SECRETS.stat()
    if st.st_uid != 0 or st.st_mode & 0o077:
        raise RuntimeError('AUTOKOM Jenkins: /etc/autokom/secrets doit appartenir à root et être privé')


def known_bootstrap_hook(path=None):
    path = Path(path) if path is not None else SCRIPT
    if path.is_symlink() or not path.is_file():
        return False
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        stat_result = path.stat()
        return (stat_result.st_uid == os.geteuid() and not stat_result.st_mode & 0o022
                and (digest in OLDER_AUTOKOM_HOOKS or digest == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()))
    except OSError:
        return False


def interrupted_managed_bootstrap():
    """Evidence to classify a partial AUTOKOM first-run, NOT permission to replace users."""
    try:
        if not (SCRIPT.is_file() and known_bootstrap_hook() and PASSWORD.is_file()):
            return False
        _secure_file(PASSWORD)
        return bool(PASSWORD.read_text(encoding='utf-8').strip())
    except (OSError, RuntimeError):
        return False


def _password():
    _private_root_directory()
    if PASSWORD.exists() or PASSWORD.is_symlink():
        _secure_file(PASSWORD)
        password = PASSWORD.read_text(encoding='utf-8').strip()
        if not 16 <= len(password) <= 256 or any(ch in password for ch in '\r\n\x00'):
            raise RuntimeError('AUTOKOM Jenkins: format du mot de passe initial invalide')
        return password
    value = secrets.token_urlsafe(40)
    _write_new(PASSWORD, value + '\n')
    return value


def selected_admin():
    """Read user-approved initial admin settings, never log a password."""
    if not PROFILE.exists() and not PROFILE.is_symlink():
        return None
    _secure_file(PROFILE)
    try:
        record = json.loads(PROFILE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError('AUTOKOM Jenkins : identité administrative sauvegardée invalide') from exc
    if not isinstance(record, dict) or record.get('schema') != 1:
        raise RuntimeError('AUTOKOM Jenkins : format identité administrative non pris en charge')
    validate_admin_identity(record.get('username', ''), record.get('full_name', ''), record.get('email', ''))
    _secure_file(PASSWORD)
    if not 16 <= len(PASSWORD.read_text(encoding='utf-8').rstrip('\n')) <= 256:
        raise RuntimeError('AUTOKOM Jenkins : mot de passe initial sauvegardé invalide')
    return record


def validate_admin_identity(username, full_name, email):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{2,39}', username or '') or username.lower() in ('admin', 'autokom-metrics'):
        raise ValueError('Identifiant Jenkins : 3-40 caractères (lettres, chiffres, _-), sauf admin temporaire et autokom-metrics')
    if not isinstance(full_name, str) or not 1 <= len(full_name.strip()) <= 100 or any(ord(ch)<32 for ch in full_name):
        raise ValueError('Nom complet Jenkins invalide (1 à 100 caractères)')
    if not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email)>254:
        raise ValueError('E-mail administrateur Jenkins invalide')


def save_admin_choice(username, full_name, email, password):
    """One explicit admin choice; preserve a legacy AUTO-generated seed safely.

    Only used on a pristine wizard, before Jenkins has created the new account.
    Existing identity and token are never silently replaced.
    """
    _private_root_directory()
    validate_admin_identity(username, full_name, email)
    if username == USER and (PASSWORD.exists() or OLD_PASSWORD.exists()):
        raise ValueError('Identifiant déjà utilisé par AUTOKOM lors de la tentative précédente : choisir un autre administrateur')
    if not isinstance(password, str) or not 16 <= len(password) <= 256 or any(ch in password for ch in '\r\n\x00'):
        raise ValueError('Mot de passe Jenkins invalide : entre 16 et 256 caractères, sans retour à la ligne')
    if selected_admin() is not None:
        raise RuntimeError('Identité Jenkins déjà sauvegardée ; aucun remplacement implicite')
    if AUTH.exists() or AUTH.is_symlink() or TOKEN.exists() or TOKEN.is_symlink():
        raise RuntimeError('Initialisation Jenkins déjà effectuée ou en cours : identités conservées')
    if PROFILE.is_symlink() or OLD_PASSWORD.exists() or OLD_PASSWORD.is_symlink():
        raise RuntimeError('Conflit de sauvegarde des identifiants Jenkins : aucun fichier remplacé')
    if SCRIPT.exists() or SCRIPT.is_symlink():
        if not known_bootstrap_hook():
            raise RuntimeError('Hook Jenkins étranger existant : aucun remplacement')
    if PASSWORD.exists() or PASSWORD.is_symlink():
        _secure_file(PASSWORD)
        old = PASSWORD.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', old):
            raise RuntimeError('Mot de passe legacy Jenkins inconnu : conserver la configuration')
        if SEED.exists() or SEED.is_symlink():
            _secure_file(SEED, pwd.getpwnam('jenkins').pw_uid)
            if SEED.read_text(encoding='utf-8') != 'autokom-admin\n' + old + '\n':
                raise RuntimeError('Seed Jenkins modifié ou en cours : aucun remplacement')
            SEED.unlink()
        # Keep previous AUTOKOM-generated credential as a root-only backup.
        os.replace(PASSWORD, OLD_PASSWORD)
    _write_new(PASSWORD, password + '\n')
    record = {'schema': 1, 'username': username, 'full_name': full_name.strip(), 'email': email.strip()}
    _write_new(PROFILE, json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
    return record


def admin_username():
    record = selected_admin()
    return record['username'] if record is not None else USER


def _stage():
    """Stage a managed Groovy hook with strict collision checks and no overwrite."""
    expected = TEMPLATE.read_text(encoding='utf-8')
    owner = pwd.getpwnam('jenkins')
    uid, gid = owner.pw_uid, owner.pw_gid
    if STAGING.is_symlink():
        raise RuntimeError('AUTOKOM Jenkins: dossier de staging en lien symbolique')
    STAGING.mkdir(mode=0o700, exist_ok=True)
    if STAGING.stat().st_uid == 0:
        os.chown(STAGING, uid, gid)
    if STAGING.stat().st_uid != uid or STAGING.stat().st_mode & 0o077:
        raise RuntimeError('AUTOKOM Jenkins: staging non privé')
    SCRIPT.parent.mkdir(mode=0o755, exist_ok=True)
    if SCRIPT.exists() or SCRIPT.is_symlink():
        if not known_bootstrap_hook():
            raise RuntimeError('AUTOKOM Jenkins: hook de démarrage existant non géré ; préservé')
        if SCRIPT.read_text(encoding='utf-8') != expected:
            # Only the EXACT previously published AUTOKOM hook can be upgraded.
            # Keep its original bytes under root:0600 for recoverability.
            _private_root_directory()
            backup = SECRETS / ('jenkins-bootstrap-hook-' + hashlib.sha256(SCRIPT.read_bytes()).hexdigest()[:16] + '.backup')
            if backup.exists() or backup.is_symlink():
                _secure_file(backup)
                if backup.read_bytes() != SCRIPT.read_bytes():
                    raise RuntimeError('AUTOKOM Jenkins: sauvegarde hook contradictoire ; aucun remplacement')
                SCRIPT.unlink()
            else:
                shutil.copy2(SCRIPT, backup, follow_symlinks=False)
                os.chmod(backup, 0o600)
                SCRIPT.unlink()
            _write_new(SCRIPT, expected, owner=0, group=gid, mode=0o640)
    else:
        _write_new(SCRIPT, expected, owner=0, group=gid, mode=0o640)
    identity = selected_admin()
    if identity is None:
        # Strictly legacy compatibility for previously managed initializations.
        seed_data = USER + '\n' + _password() + '\n'
    else:
        seed_data = (identity['username'] + '\n' + _password() + '\n' +
                     identity['full_name'] + '\n' + identity['email'] + '\n')
    if SEED.exists() or SEED.is_symlink():
        _secure_file(SEED, uid)
        if SEED.read_text(encoding='utf-8') != seed_data:
            raise RuntimeError('AUTOKOM Jenkins: seed de démarrage différent ; préservé')
    else:
        _write_new(SEED, seed_data, owner=uid, group=gid)
    # A prior AUTOKOM revision may have created 'autokom-admin' before its
    # first-boot token could be recovered. Prove that *specific* existing
    # identity with its unchanged old password; never reset unknown users.
    if OLD_PASSWORD.is_file() and not OLD_PASSWORD.is_symlink():
        _secure_file(OLD_PASSWORD)
        old = OLD_PASSWORD.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', old):
            raise RuntimeError('AUTOKOM Jenkins: mot de passe précédent non vérifiable')
        proof = USER + '\n' + old + '\n'
        if LEGACY_PROOF.exists() or LEGACY_PROOF.is_symlink():
            _secure_file(LEGACY_PROOF, uid)
            if LEGACY_PROOF.read_text(encoding='utf-8') != proof:
                raise RuntimeError('AUTOKOM Jenkins: preuve historique incompatible ; aucun compte remplacé')
        else:
            _write_new(LEGACY_PROOF, proof, owner=uid, group=gid)


def _recover_auth():
    """Only promote the token created by the protected AUTOKOM hook."""
    _private_root_directory()
    if AUTH.exists() or AUTH.is_symlink():
        _secure_file(AUTH)
        return AUTH
    _secure_file(TOKEN, pwd.getpwnam('jenkins').pw_uid)
    raw = TOKEN.read_text(encoding='utf-8')
    name = admin_username()
    if not re.fullmatch(re.escape(name) + r':[A-Za-z0-9_-]{20,180}\n', raw):
        raise RuntimeError('AUTOKOM Jenkins: jeton bootstrap invalide ; aucun fichier modifié')
    _write_new(AUTH, raw)
    TOKEN.unlink()
    SEED.unlink(missing_ok=True)
    LEGACY_PROOF.unlink(missing_ok=True)
    if SCRIPT.is_file() and not SCRIPT.is_symlink() and SCRIPT.read_text(encoding='utf-8') == TEMPLATE.read_text(encoding='utf-8'):
        SCRIPT.unlink()
    return AUTH


def auth_file_if_available():
    """Recover an interrupted completed bootstrap, without changing Jenkins users."""
    if AUTH.exists() or AUTH.is_symlink():
        _secure_file(AUTH)
        return str(AUTH)
    if TOKEN.is_file() and _configured_secure() and _probe_token():
        return str(_recover_auth())
    return ''




def _configured_secure():
    """Check security realm without relying solely on the setup-wizard marker."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(JENKINS_HOME / 'config.xml').getroot()
        realm = root.find('securityRealm')
        strategy = root.find('authorizationStrategy')
        return (root.findtext('useSecurity') != 'false' and realm is not None
                and strategy is not None and 'None' not in realm.attrib.get('class', '')
                and 'Unsecured' not in strategy.attrib.get('class', ''))
    except (ET.ParseError, OSError):
        return False


def _probe_token():
    """A successful private authenticated Jenkins whoAmI is required before recovery."""
    if not TOKEN.is_file():
        return False
    _secure_file(TOKEN, pwd.getpwnam('jenkins').pw_uid)
    value = TOKEN.read_text(encoding='utf-8').strip()
    if not re.fullmatch(re.escape(admin_username()) + r':[A-Za-z0-9_-]{20,180}', value):
        raise RuntimeError('AUTOKOM Jenkins: jeton inattendu ; initialisation arrêtée')
    auth = base64.b64encode(value.encode('utf-8')).decode('ascii')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    req = urllib.request.Request('http://127.0.0.1:8080/whoAmI/api/json',
                                 headers={'Authorization': 'Basic ' + auth, 'Accept': 'application/json'})
    try:
        with opener.open(req, timeout=4) as response:
            return response.status == 200 and json.loads(response.read(16384).decode('utf-8')).get('authenticated') is True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _boot_failure_excerpt(started_at):
    """Only return a stable error code; never leak full Jenkins service logs."""
    try:
        proc = subprocess.run(['journalctl', '-u', 'jenkins', '--since', '@' + str(int(started_at)), '-n', '110', '--no-pager', '-o', 'cat'],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    for line in reversed(proc.stdout.splitlines()):
        if 'AUTOKOM_FIRSTBOOT:' in line:
            return line.split('AUTOKOM_FIRSTBOOT', 1)[1][:150].split('\n', 1)[0]
    return ''

def bootstrap(*, approved, timeout=180, status=None):
    """Restart only after explicit administrator approval (provided by CLI stage)."""
    if not approved:
        raise RuntimeError('AUTOKOM Jenkins: redémarrage non autorisé par l’administrateur')
    if os.geteuid() != 0:
        raise RuntimeError('AUTOKOM Jenkins: sudo requis')
    if (JENKINS_HOME / 'jenkins.install.UpgradeWizard.state').exists():
        existing = auth_file_if_available()
        if existing:
            return existing
        raise RuntimeError('AUTOKOM Jenkins: contrôleur déjà initialisé ; comptes existants préservés')
    initial = JENKINS_HOME / 'secrets/initialAdminPassword'
    if not initial.is_file() or initial.is_symlink():
        raise RuntimeError('AUTOKOM Jenkins: assistant initial absent ; aucun compte modifié')
    if selected_admin() is None:
        raise RuntimeError('AUTOKOM Jenkins : définir le premier administrateur dans le terminal avant le redémarrage')
    _stage()
    if status:
        status('Redémarrage Jenkins · création automatique du premier administrateur')
    started_at = time.time()
    result = subprocess.run(['systemctl', 'restart', 'jenkins'], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('AUTOKOM Jenkins: redémarrage échoué ; consulter journalctl -u jenkins')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if TOKEN.is_file() and _configured_secure() and _probe_token():
            return str(_recover_auth())
        detail = _boot_failure_excerpt(started_at)
        if detail:
            raise RuntimeError('AUTOKOM Jenkins : erreur dans init.groovy.d (' + detail + '). Examiner : journalctl -u jenkins -n 100 --no-pager')
        if subprocess.run(['systemctl', 'is-active', '--quiet', 'jenkins'], capture_output=True).returncode != 0:
            raise RuntimeError('AUTOKOM Jenkins : service arrêté avant la fin du bootstrap ; consulter journalctl -u jenkins')
        time.sleep(3)
    raise RuntimeError('AUTOKOM Jenkins: délai dépassé, compte ou API non confirmé ; consulter journalctl -u jenkins -n 100 --no-pager. Secrets et choix conservés pour reprise.')


def stage_matrix_restriction(*, include_metrics=False):
    """Stage least-privilege only for the AUTOKOM-created initial admin.

    Runs when Matrix Authorization loads on the Jenkins plugin safe-restart.
    Foreign Groovy hooks are never overwritten.
    """
    if not AUTH.is_file():
        raise RuntimeError('AUTOKOM Jenkins: token initial absent ; configuration Matrix refusée')
    _secure_file(AUTH)
    expected = MATRIX_TEMPLATE.read_text(encoding='utf-8')
    uid, gid = (pwd.getpwnam('jenkins').pw_uid, pwd.getpwnam('jenkins').pw_gid)
    if MATRIX_SCRIPT.exists() or MATRIX_SCRIPT.is_symlink():
        if MATRIX_SCRIPT.is_symlink() or MATRIX_SCRIPT.read_text(encoding='utf-8') != expected:
            raise RuntimeError('AUTOKOM Jenkins: script Matrix étranger conservé')
    else:
        MATRIX_SCRIPT.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        _write_new(MATRIX_SCRIPT, expected, owner=0, group=gid, mode=0o640)
    if MATRIX_PENDING.exists() or MATRIX_PENDING.is_symlink():
        _secure_file(MATRIX_PENDING, uid)
        if MATRIX_PENDING.read_text(encoding='utf-8') != admin_username() + '\n':
            raise RuntimeError('AUTOKOM Jenkins: marqueur Matrix étranger conservé')
    else:
        _write_new(MATRIX_PENDING, admin_username() + '\n', owner=uid, group=gid)
    identity = selected_admin()
    if identity:
        data = identity['username'] + '\n' + identity['email'] + '\n'
        if ADMIN_MAIL_PROFILE.exists() or ADMIN_MAIL_PROFILE.is_symlink():
            _secure_file(ADMIN_MAIL_PROFILE, uid)
            if ADMIN_MAIL_PROFILE.read_text(encoding='utf-8') != data:
                raise RuntimeError('AUTOKOM Jenkins : profil e-mail déjà préparé différent ; aucune modification')
        else:
            _write_new(ADMIN_MAIL_PROFILE, data, owner=uid, group=gid)
    if include_metrics:
        _private_root_directory()
        if METRICS_AUTH.exists() or METRICS_AUTH.is_symlink():
            raise RuntimeError('AUTOKOM Jenkins: jeton Metrics existant ; ne pas réinitialiser le compte')
        if METRICS_PASSWORD.exists() or METRICS_PASSWORD.is_symlink():
            _secure_file(METRICS_PASSWORD)
            password = METRICS_PASSWORD.read_text(encoding='utf-8').strip()
        else:
            password = secrets.token_urlsafe(40)
            _write_new(METRICS_PASSWORD, password + '\n')
        if len(password) < 32 or '\n' in password:
            raise RuntimeError('AUTOKOM Jenkins: mot de passe Metrics invalide')
        seed_data = 'autokom-metrics\n' + password + '\n'
        if METRICS_SEED.exists() or METRICS_SEED.is_symlink():
            _secure_file(METRICS_SEED, uid)
            if METRICS_SEED.read_text(encoding='utf-8') != seed_data:
                raise RuntimeError('AUTOKOM Jenkins: seed Metrics existant différent')
        else:
            _write_new(METRICS_SEED, seed_data, owner=uid, group=gid)


def matrix_restriction_pending():
    return MATRIX_PENDING.is_file() and not MATRIX_DONE.is_file()


def finalize_matrix_restriction():
    """Require proven completion before AUTOKOM announces plugin-stage success."""
    if not MATRIX_PENDING.exists():
        return
    _secure_file(MATRIX_PENDING, pwd.getpwnam('jenkins').pw_uid)
    if not MATRIX_DONE.is_file():
        raise RuntimeError('Jenkins Matrix Authorization non confirmée après redémarrage ; consulter journalctl -u jenkins')
    _secure_file(MATRIX_DONE, pwd.getpwnam('jenkins').pw_uid)
    from xml.etree import ElementTree as ET
    config = JENKINS_HOME / 'config.xml'
    try:
        root = ET.parse(config).getroot()
        strategy = root.find('authorizationStrategy')
    except (ET.ParseError, OSError):
        raise RuntimeError('AUTOKOM Jenkins: configuration Matrix illisible') from None
    if strategy is None or strategy.attrib.get('class') != 'hudson.security.GlobalMatrixAuthorizationStrategy':
        raise RuntimeError('AUTOKOM Jenkins: stratégie Matrix non confirmée sur disque')
    if not any('Administer:' + admin_username() in (e.text or '') for e in strategy.iter()):
        raise RuntimeError('AUTOKOM Jenkins: droits administrateur AUTOKOM non confirmés')
    if LEGACY_PRESERVED.exists() or LEGACY_PRESERVED.is_symlink():
        _secure_file(LEGACY_PRESERVED, pwd.getpwnam('jenkins').pw_uid)
        if LEGACY_PRESERVED.read_text(encoding='utf-8') != USER + '\n':
            raise RuntimeError('AUTOKOM Jenkins: ancien compte conservé non reconnu')
        if not any('Administer:' + USER in (e.text or '') for e in strategy.iter()):
            raise RuntimeError('AUTOKOM Jenkins: droits de l’ancien administrateur non conservés')
    if METRICS_PASSWORD.exists() and not METRICS_AUTH.exists():
        _secure_file(METRICS_TOKEN, pwd.getpwnam('jenkins').pw_uid)
        raw = METRICS_TOKEN.read_text(encoding='utf-8')
        if not re.fullmatch(r'autokom-metrics:[A-Za-z0-9_-]{20,180}\n', raw):
            raise RuntimeError('AUTOKOM Jenkins: jeton Metrics généré invalide')
        if not any('Metrics.View:autokom-metrics' in (e.text or '') for e in strategy.iter()):
            raise RuntimeError('AUTOKOM Jenkins: droit Metrics.View non confirmé')
        _write_new(METRICS_AUTH, raw)
        METRICS_TOKEN.unlink()
    expected = MATRIX_TEMPLATE.read_text(encoding='utf-8')
    if MATRIX_SCRIPT.is_file() and not MATRIX_SCRIPT.is_symlink() and MATRIX_SCRIPT.read_text(encoding='utf-8') == expected:
        MATRIX_SCRIPT.unlink()
    MATRIX_PENDING.unlink()
    MATRIX_DONE.unlink()
    ADMIN_MAIL_PROFILE.unlink(missing_ok=True)
