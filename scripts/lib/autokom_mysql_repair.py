#!/usr/bin/env python3
"""AUTOKOM Platform: consensual MySQL repair, separate from deploy pipeline.

Only local MySQL root socket is supported for account provisioning. No root
password, SQL password, or application credential is passed in argv or logged.
No existing account is altered. Dedicated accounts receive DB-scoped grants.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

DB_FIELDS = ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')
DEFAULT_SOURCE = Path('/etc/autokom/secrets/backend.env')
DEDICATED_USER = 'autokom_backend'
CLIENT_TIMEOUT = 18


def _valid(config):
    db = str(config.get('DB_NAME', ''))
    user = str(config.get('DB_USER', ''))
    return (re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', db) is not None
            and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', user) is not None
            and config.get('DB_HOST') in ('127.0.0.1', 'localhost')
            and str(config.get('DB_PORT', '3306')) == '3306')


def _yesno(prompt):
    """Choix AUTOKOM animé, refus par défaut ; aucune action sur annulation."""
    from menu import pick
    try:
        selected = pick('AUTOKOM · ' + prompt,
                        ['Non, ne rien modifier', 'Oui, vérifier et proposer la réparation'],
                        default=1, cancel_label='Annuler sans modification')
        return selected == 2
    except (KeyboardInterrupt, EOFError):
        print('\n  › Opération annulée ; aucun changement MySQL.')
        return False
    except RuntimeError as exc:
        print('  › Terminal interactif indisponible ; aucune modification MySQL.')
        return False


def _confirm_word(word):
    try:
        return input(f'  Taper {word} pour confirmer : ').strip() == word
    except (KeyboardInterrupt, EOFError):
        print('\n  › Confirmation annulée.')
        return False


def _subprocess_json(python, mode, payload):
    # The JSON payload (including secrets) goes through stdin only.
    try:
        proc = subprocess.run(
            [str(python), str(Path(__file__).resolve()), '--internal-' + mode],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=CLIENT_TIMEOUT, check=False,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        )
    except (OSError, subprocess.TimeoutExpired):
        return {'ok': False, 'error': 'execution'}
    try:
        answer = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return {'ok': False, 'error': 'execution'}
    return answer if isinstance(answer, dict) else {'ok': False, 'error': 'execution'}


def _probe_mysql(python, config):
    return _subprocess_json(python, 'probe', {key: config.get(key, '') for key in DB_FIELDS})


def _admin_inspect(python, config):
    return _subprocess_json(python, 'inspect', {'DB_NAME': config['DB_NAME'], 'DB_USER': DEDICATED_USER})


def _new_dedicated_user(python, config, password):
    return _subprocess_json(python, 'create', {
        'DB_NAME': config['DB_NAME'], 'DB_USER': DEDICATED_USER, 'DB_PASSWORD': password,
    })


def _atomic_update_source(path, credentials):
    """Sync dedicated backend source file, not repository code or env examples."""
    path = Path(path)
    if path != DEFAULT_SOURCE or path.is_symlink() or not path.is_file():
        return False
    if path.stat().st_uid != 0:
        raise RuntimeError('Fichier source .env non détenu par root.')
    data = path.read_text(encoding='utf-8')
    out = []
    found = set()
    for line in data.splitlines():
        key = line.split('=', 1)[0].strip()
        if key in ('DB_USER', 'DB_PASSWORD') and '=' in line and key not in found:
            out.append(f'{key}={credentials[key]}')
            found.add(key)
        else:
            out.append(line)
    for key in ('DB_USER', 'DB_PASSWORD'):
        if key not in found:
            out.append(f'{key}={credentials[key]}')
    if path.parent.is_symlink():
        raise RuntimeError('Répertoire source .env symbolique refusé.')
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.backend-env-', text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(out) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def offer_mysql_repair(*, runtime_file, source_file, python, load_config,
                       save_config, verify):
    """Prompt only after failed MySQL check; return True after verification.

    Reauth is delegated to AUTOKOM's outer command passphrase. The two explicit
    confirmations here authorize the *database changes* separately.
    """
    if os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
        print('  › Réparation interactive : sudo autokom app doctor')
        return False

    print('  ✗ Connexion MySQL refusée. Aucune migration n’a été exécutée.')
    if not _yesno('Vérifier et proposer une configuration automatique MySQL ?'):
        return False

    runtime = load_config(Path(runtime_file))
    source_path = Path(source_file)
    source = load_config(source_path) if source_path.is_file() and not source_path.is_symlink() else {}
    if not _valid(runtime):
        print('  ✗ Réparation automatique limitée à MySQL local (127.0.0.1:3306), noms valides.')
        return False

    if source and _valid(source) and any(source.get(k) != runtime.get(k) for k in DB_FIELDS):
        probe = _probe_mysql(python, source)
        if probe.get('ok'):
            print('  ✓ Les identifiants MySQL du backend.env source fonctionnent.')
            if not _confirm_word('SYNCHRONISER'):
                return False
            updated = dict(runtime)
            for key in DB_FIELDS:
                updated[key] = source[key]
            save_config(updated)
            try:
                verify()
            except Exception:
                print('  ✗ Vérification Django toujours en échec ; consulter le diagnostic.')
                return False
            if source_path == DEFAULT_SOURCE and source_path.is_file() and not source_path.is_symlink():
                os.chmod(source_path, 0o600)
            print('  ✓ Identifiants du service synchronisés, connexion Django validée.')
            return True

    if _probe_mysql(python, runtime).get('ok'):
        # If MySQL credentials succeed outside Django, do not touch MySQL users.
        print('  ! MySQL accepte les identifiants ; vérifier les paramètres Django plutôt que modifier MySQL.')
        return False

    inspect = _admin_inspect(python, runtime)
    if not inspect.get('ok'):
        print('  ✗ Administration MySQL locale par socket indisponible. Aucune modification réalisée.')
        return False
    if inspect.get('account_exists'):
        print('  ! Le compte MySQL dédié existe déjà. Aucun mot de passe existant ne sera réinitialisé.')
        return False

    print('  ◈ Proposition · Réparation MySQL sûre')
    print('  Base ciblée :', runtime['DB_NAME'], '(existante)' if inspect.get('db_exists') else '(à créer)')
    print('  Nouvel utilisateur applicatif dédié :', DEDICATED_USER)
    print('  Accès : localhost et 127.0.0.1 · droits limités à cette base')
    print('  Mot de passe : généré aléatoirement, conservé uniquement dans les fichiers .env root:0600')
    print('  Compte actuel : inchangé · aucune table supprimée · aucune migration exécutée')
    if not _confirm_word('CONFIGURER'):
        print('  › Aucune modification MySQL.')
        return False

    password = secrets.token_urlsafe(40)
    result = _new_dedicated_user(python, runtime, password)
    if not result.get('ok'):
        print('  ✗ Création du compte dédié impossible. Consulter les journaux MySQL administrateur.')
        return False
    updated = dict(runtime)
    updated.update(DB_USER=DEDICATED_USER, DB_PASSWORD=password)
    try:
        save_config(updated)
        verify()
    except Exception:
        print('  ✗ Le compte a été créé, mais la nouvelle configuration Django doit être vérifiée.')
        print('  › Aucun ancien compte MySQL n’a été modifié.')
        return False
    try:
        changed = _atomic_update_source(source_path, {'DB_USER': DEDICATED_USER, 'DB_PASSWORD': password})
    except (OSError, RuntimeError):
        changed = False
    if not changed:
        print('  ! Le backend.env source n’a pas pu être synchronisé ; conserver le .env du service comme référence.')
    print('  ✓ Connexion MySQL réparée et validée par Django (SELECT 1).')
    return True


def _admin_connection():
    import MySQLdb
    for socket in ('/var/run/mysqld/mysqld.sock', '/run/mysqld/mysqld.sock'):
        if os.path.exists(socket):
            return MySQLdb.connect(user='root', unix_socket=socket, connect_timeout=5)
    raise RuntimeError('Socket MySQL absent')


def _internal_worker(mode):
    """Isolated MySQLDB worker; never prints payloads or exceptions."""
    try:
        import MySQLdb
        payload = json.loads(sys.stdin.read(8192))
        if mode == 'probe':
            con = MySQLdb.connect(
                host=payload['DB_HOST'], port=int(payload.get('DB_PORT', 3306)),
                user=payload['DB_USER'], passwd=payload['DB_PASSWORD'],
                db=payload['DB_NAME'], connect_timeout=5,
            )
            try:
                with con.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    ok = cursor.fetchone()[0] == 1
            finally:
                con.close()
            return {'ok': ok}

        db, user = payload['DB_NAME'], payload['DB_USER']
        if (re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', db) is None or
                user != DEDICATED_USER or os.geteuid() != 0):
            return {'ok': False, 'error': 'invalid'}
        con = _admin_connection()
        try:
            with con.cursor() as cursor:
                cursor.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (db,))
                db_exists = bool(cursor.fetchone())
                cursor.execute("SELECT Host FROM mysql.user WHERE User=%s AND Host IN ('localhost','127.0.0.1')", (user,))
                account_exists = bool(cursor.fetchall())
                if mode == 'inspect':
                    return {'ok': True, 'db_exists': db_exists, 'account_exists': account_exists}
                if mode != 'create' or account_exists:
                    return {'ok': False, 'error': 'already_exists'}
                password = payload['DB_PASSWORD']
                if len(password) < 40:
                    return {'ok': False, 'error': 'invalid_password'}
                # DBA action only after explicit admin confirmation, local socket.
                if not db_exists:
                    cursor.execute(f'CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
                for host in ('localhost', '127.0.0.1'):
                    cursor.execute('CREATE USER %s@%s IDENTIFIED BY %s', (user, host, password))
                    cursor.execute(
                        f'GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES ON `{db}`.* TO %s@%s',
                        (user, host),
                    )
                return {'ok': True}
        finally:
            con.close()
    except Exception as exc:
        # Return only numeric MySQL codes, never raw server errors (may contain credentials).
        code = exc.args[0] if getattr(exc, 'args', None) and isinstance(exc.args[0], int) else None
        return {'ok': False, 'code': code, 'error': 'mysql'}


if __name__ == '__main__':
    modes = {'--internal-probe': 'probe', '--internal-inspect': 'inspect', '--internal-create': 'create'}
    mode = modes.get(sys.argv[1]) if len(sys.argv) == 2 else None
    if not mode:
        sys.exit('Module AUTOKOM interne ; utiliser autokom app doctor/deploy.')
    result = _internal_worker(mode)
    print(json.dumps(result))
    sys.exit(0 if result.get('ok') else 1)
