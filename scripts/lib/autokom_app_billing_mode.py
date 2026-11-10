#!/usr/bin/env python3
"""Switch AUTOKOM *billing simulation only* using the guarded backend env.

This is NOT a Django DEBUG/production switch.  Never writes application
secrets to stdout or the Git repository.  The test allowlist is compulsory.
"""
from __future__ import annotations

import fcntl
import os
import re
import subprocess
from pathlib import Path

from autokom_env_manager import (EnvError, _systemd_override, _validate, backup, file_path,
                                 read_env, require_root, write_env)

BACKEND_SERVICE = 'autokom-backend.service'
LOCK = Path('/run/autokom-billing-admin/mode.lock')
ENABLED_KEY = 'AUTOKOM_BILLING_SANDBOX_ENABLED'
EMAIL_KEY = 'AUTOKOM_BILLING_SANDBOX_TEST_EMAILS'
_EMAIL = re.compile(r'[^\s@,]+@[^\s@,]+\.[^\s@,]+\Z')


class BillingModeError(RuntimeError):
    pass


def _run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BillingModeError('Impossible de contacter systemd : ' + type(exc).__name__) from exc


def _emails(raw):
    addresses = [item.strip().casefold() for item in raw.split(',') if item.strip()]
    if not addresses or len(addresses) > 20 or any(len(item) > 254 or not _EMAIL.fullmatch(item) for item in addresses):
        raise BillingModeError('Indiquer entre 1 et 20 adresses e-mail de comptes de TEST valides.')
    # Keep the list deterministic and duplicate-free.
    return ','.join(dict.fromkeys(addresses))


def _existing(path):
    if path.is_symlink() or not path.is_file():
        raise BillingModeError('Configuration du backend introuvable ou lien symbolique refusé : '
                               '/etc/autokom/secrets/backend.env')
    if path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise BillingModeError('backend.env doit appartenir à root avec les permissions 0600.')
    return read_env(path)


def _loaded_environment_files():
    """Read paths only: systemctl does not expose backend.env secret values."""
    result = _run('/usr/bin/systemctl', 'show', '--value', '-p',
                  'EnvironmentFiles', BACKEND_SERVICE)
    if result.returncode:
        raise BillingModeError('Impossible de vérifier EnvironmentFiles du backend.')
    # systemd outputs: /path/file (ignore_errors=no) ...
    return re.findall(r'(/[^\s]+)\s+\(ignore_errors=(?:yes|no)\)', result.stdout)


def _ensure_service_env():
    """Wire backend.env to the actual unit before restarting Gunicorn."""
    expected = str(file_path('backend'))
    files = _loaded_environment_files()
    if files and files[-1] == expected:
        return
    # Repair legacy units or overrides which still reference a different env.
    # Refuse to continue if a later override defeats the managed drop-in.
    if not _systemd_override('autokom-backend', file_path('backend')):
        raise BillingModeError('Unité systemd du backend absente ; exécuter autokom app deploy.')
    if _run('/usr/bin/systemctl', 'daemon-reload').returncode:
        raise BillingModeError('Impossible de recharger la définition systemd du backend.')
    files = _loaded_environment_files()
    if not files or files[-1] != expected:
        raise BillingModeError('Le backend n’utilise pas backend.env : vérifier ses overrides systemd.')


def _django_env_probe(expected):
    """Verify systemd parses the secure env file and Django sees the requested mode.

    Gunicorn (notably setproctitle) may overwrite /proc/MainPID/environ, so
    that pseudo-file must never be the sole ground truth for effective settings.
    This transient unit uses the backend's service user, env file, directory,
    interpreter and Django settings module; it never outputs credentials.
    """
    from autokom_app_manager import BACKEND, VENV, USER
    code = ('import sys; from config import settings; '
            'want = sys.argv[1] == "true"; '
            'actual = bool(settings.AUTOKOM_BILLING_SANDBOX_ENABLED); '
            'allowed = settings.AUTOKOM_BILLING_SANDBOX_TEST_EMAILS; '
            'sys.exit(0 if actual == want and (not want or bool(allowed)) else 3)')
    args = ('/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--unit=autokom-billing-probe-' + str(os.getpid()),
            '--property=User=' + USER,
            '--property=WorkingDirectory=' + str(BACKEND),
            '--property=EnvironmentFile=' + str(file_path('backend')),
            str(VENV / 'bin/python'), '-c', code, expected)
    result = _run(*args)
    if result.returncode:
        raise BillingModeError(
            'Vérification Django via systemd en échec : contrôler backend.env, '
            'les droits du compte autokom et journalctl -u autokom-billing-probe-*.service. '
            'Aucun secret affiché.')


def _restart_and_check(expected):
    _ensure_service_env()
    proc = _run('/usr/bin/systemctl', 'restart', BACKEND_SERVICE)
    if proc.returncode:
        raise BillingModeError('Redémarrage du backend refusé (voir journalctl -u autokom-backend.service).')
    proc = _run('/usr/bin/systemctl', 'is-active', '--quiet', BACKEND_SERVICE)
    if proc.returncode:
        raise BillingModeError('Le backend n’est pas actif après le redémarrage.')
    pid_result = _run('/usr/bin/systemctl', 'show', '--value', '-p', 'MainPID', BACKEND_SERVICE)
    try:
        pid = int(pid_result.stdout.strip()) if pid_result.returncode == 0 else 0
    except ValueError:
        pid = 0
    if pid <= 1:
        raise BillingModeError('PID du processus Django introuvable après redémarrage.')
    # Check Django settings using the same systemd environment rather than
    # /proc/PID/environ, which process-title libraries can overwrite.
    _django_env_probe('false' if expected is None else expected)


def status():
    try:
        require_root()
    except EnvError as exc:
        raise BillingModeError(str(exc)) from exc
    values = _existing(file_path('backend'))
    enabled = values.get(ENABLED_KEY, 'false').strip().lower() in ('1', 'true', 'yes', 'on')
    count = len([e for e in values.get(EMAIL_KEY, '').split(',') if e.strip()])
    service = _run('/usr/bin/systemctl', 'is-active', '--quiet', BACKEND_SERVICE)
    print('AUTOKOM · Abonnements : ' + ('TEST (paiement simulé, sans débit)' if enabled else 'PRODUCTION (simulation désactivée)'))
    print('  Comptes de test autorisés : ' + str(count) + ' (adresses masquées)')
    print('  Backend : ' + ('actif' if service.returncode == 0 else 'inactif'))
    return 0


def switch(mode, *, prompt=input):
    if mode not in ('test', 'prod'):
        raise BillingModeError('Mode inconnu : test ou prod attendu.')
    try:
        require_root()
    except EnvError as exc:
        raise BillingModeError(str(exc)) from exc
    # Serialize concurrent calls, including test-mode updates from two shells.
    if LOCK.parent.is_symlink():
        raise BillingModeError('Répertoire de verrouillage symbolique refusé.')
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if LOCK.parent.stat().st_uid != 0 or LOCK.parent.stat().st_mode & 0o077:
        raise BillingModeError('Verrouillage : le répertoire doit appartenir à root (0700).')
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'r+b', closefd=True) as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            path = file_path('backend')
            values = _existing(path)
            if _run('/usr/bin/systemctl', 'is-active', '--quiet', BACKEND_SERVICE).returncode:
                raise BillingModeError('Le backend est inactif : vérifier son déploiement avant de modifier le mode.')
            updated = dict(values)
            if mode == 'test':
                raw = values.get(EMAIL_KEY, '').strip()
                if not raw:
                    if not os.isatty(0):
                        raise BillingModeError('Définir d’abord les e-mails autorisés via : '
                            'sudo autokom app backend env edit AUTOKOM_BILLING_SANDBOX_TEST_EMAILS')
                    raw = prompt('Compte(s) autorisé(s) au paiement simulé (e-mails séparés par virgule) : ').strip()
                updated[EMAIL_KEY] = _emails(raw)
            updated[ENABLED_KEY] = 'true' if mode == 'test' else 'false'
            try:
                _validate('backend', updated)
            except (EnvError, ValueError) as exc:
                raise BillingModeError(str(exc)) from exc
            changed = updated != values
            if changed:
                try:
                    backup(path)
                    write_env(path, updated)
                except (EnvError, OSError) as exc:
                    raise BillingModeError('Sauvegarde de la configuration refusée : ' + str(exc)) from exc
            try:
                _restart_and_check(updated[ENABLED_KEY])
            except BillingModeError as exc:
                if changed:
                    try:
                        # Rollback to the exact previous values, atomically.
                        write_env(path, values)
                        _restart_and_check(values.get(ENABLED_KEY))
                        raise BillingModeError(str(exc) + ' Configuration précédente restaurée.') from exc
                    except (OSError, EnvError, BillingModeError) as rollback_error:
                        if isinstance(rollback_error, BillingModeError) and 'restaurée.' in str(rollback_error):
                            raise
                        raise BillingModeError(str(exc) + ' ATTENTION : vérifier la configuration et le service ; '
                                               'retour arrière non confirmé.') from rollback_error
                raise
            if mode == 'test':
                count = len(updated[EMAIL_KEY].split(','))
                print('  ✓ AUTOKOM · MODE TEST activé · simulation de paiement uniquement.')
                print(f'  ✓ {count} compte(s) explicitement autorisé(s) ; aucun argent débité.')
                print('  ✓ Backend redémarré et configuration Django vérifiée via systemd.')
            else:
                print('  ✓ AUTOKOM · MODE PRODUCTION activé · nouveau paiement simulé interdit.')
                print('  ✓ Les droits TEST sont refusés aux nouvelles finalisations/deploiements.')
                print('  ✓ Les reçus TEST historiques restent en base pour audit ; les sites déjà actifs ne sont pas arrêtés.')
                print('  ✓ Backend redémarré et configuration Django vérifiée via systemd.')
            return 0
    except OSError as exc:
        raise BillingModeError('Fichier de verrouillage indisponible : ' + type(exc).__name__) from exc
