#!/usr/bin/env python3
"""One-time Jenkins admin bootstrap for an unconfigured, stock wizard only.

No Jenkins passwords or API tokens are printed or put in process arguments.
Secrets remain root:0600; Jenkins sees a transient local seed only at restart.
"""
import os
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
PASSWORD = SECRETS / 'jenkins-first-admin.password'
AUTH = SECRETS / 'jenkins-admin.auth'
STAGING = JENKINS_HOME / '.autokom-firstboot'
SEED = STAGING / 'admin.seed'
TOKEN = STAGING / 'admin.auth.pending'
SCRIPT = JENKINS_HOME / 'init.groovy.d' / '95-autokom-first-admin.groovy'
TEMPLATE = ROOT / 'config/jenkins/first-admin-bootstrap.groovy'
MATRIX_TEMPLATE = ROOT / 'config/jenkins/first-admin-matrix.groovy'
MATRIX_SCRIPT = JENKINS_HOME / 'init.groovy.d/96-autokom-first-admin-matrix.groovy'
MATRIX_PENDING = STAGING / 'matrix.pending'
MATRIX_DONE = STAGING / 'matrix.done'
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


def _password():
    _private_root_directory()
    if PASSWORD.exists() or PASSWORD.is_symlink():
        _secure_file(PASSWORD)
        password = PASSWORD.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', password):
            raise RuntimeError('AUTOKOM Jenkins: format du mot de passe initial invalide')
        return password
    value = secrets.token_urlsafe(40)
    _write_new(PASSWORD, value + '\n')
    return value


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
        if SCRIPT.is_symlink() or SCRIPT.read_text(encoding='utf-8') != expected:
            raise RuntimeError('AUTOKOM Jenkins: hook de démarrage existant non géré ; préservé')
    else:
        _write_new(SCRIPT, expected, owner=0, group=gid, mode=0o640)
    seed_data = USER + '\n' + _password() + '\n'
    if SEED.exists() or SEED.is_symlink():
        _secure_file(SEED, uid)
        if SEED.read_text(encoding='utf-8') != seed_data:
            raise RuntimeError('AUTOKOM Jenkins: seed de démarrage différent ; préservé')
    else:
        _write_new(SEED, seed_data, owner=uid, group=gid)


def _recover_auth():
    """Only promote the token created by the protected AUTOKOM hook."""
    _private_root_directory()
    if AUTH.exists() or AUTH.is_symlink():
        _secure_file(AUTH)
        return AUTH
    _secure_file(TOKEN, pwd.getpwnam('jenkins').pw_uid)
    raw = TOKEN.read_text(encoding='utf-8')
    if not re.fullmatch(r'autokom-admin:[A-Za-z0-9]{20,180}\n', raw):
        # Jenkins token format may include hyphens; accept these as well.
        if not re.fullmatch(r'autokom-admin:[A-Za-z0-9_-]{20,180}\n', raw):
            raise RuntimeError('AUTOKOM Jenkins: jeton bootstrap invalide ; aucun fichier modifié')
    _write_new(AUTH, raw)
    TOKEN.unlink()
    SEED.unlink(missing_ok=True)
    if SCRIPT.is_file() and not SCRIPT.is_symlink() and SCRIPT.read_text(encoding='utf-8') == TEMPLATE.read_text(encoding='utf-8'):
        SCRIPT.unlink()
    return AUTH


def auth_file_if_available():
    """Recover an interrupted completed bootstrap, without changing Jenkins users."""
    if AUTH.exists() or AUTH.is_symlink():
        _secure_file(AUTH)
        return str(AUTH)
    if TOKEN.is_file() and (JENKINS_HOME / 'jenkins.install.UpgradeWizard.state').is_file():
        return str(_recover_auth())
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
    _stage()
    if status:
        status('Redémarrage Jenkins · création automatique du premier administrateur')
    result = subprocess.run(['systemctl', 'restart', 'jenkins'], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('AUTOKOM Jenkins: redémarrage échoué ; consulter journalctl -u jenkins')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if TOKEN.is_file() and (JENKINS_HOME / 'jenkins.install.UpgradeWizard.state').is_file():
            return str(_recover_auth())
        time.sleep(2)
    raise RuntimeError('AUTOKOM Jenkins: initialisation automatique non confirmée ; consulter journalctl -u jenkins. Secrets et choix conservés pour reprise.')


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
        if MATRIX_PENDING.read_text(encoding='utf-8') != 'autokom-admin\n':
            raise RuntimeError('AUTOKOM Jenkins: marqueur Matrix étranger conservé')
    else:
        _write_new(MATRIX_PENDING, 'autokom-admin\n', owner=uid, group=gid)
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
    if not any('Administer:autokom-admin' in (e.text or '') for e in strategy.iter()):
        raise RuntimeError('AUTOKOM Jenkins: droits administrateur AUTOKOM non confirmés')
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
