#!/usr/bin/env python3
"""Administrateurs Django AUTOKOM via CLI privilégiée uniquement.

Aucun mot de passe ne figure dans argv, dans un fichier .env ou dans les logs.
Django s'exécute sous l'identité de service pour systemd ; Docker conserve
l'utilisateur non privilégié défini dans le Dockerfile.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys


class AdminError(RuntimeError):
    pass


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _email(value: str | None) -> str:
    if not value:
        if not _interactive():
            raise AdminError('Adresse e-mail requise (--email) ou terminal interactif.')
        value = input('  E-mail administrateur : ').strip()
    value = value.strip().lower()
    if not value or len(value) > 254 or '\n' in value or '\r' in value:
        raise AdminError('Adresse e-mail invalide.')
    return value


def _password() -> str:
    if not _interactive():
        raise AdminError('Le mot de passe doit être saisi dans un terminal interactif.')
    first = getpass.getpass('  🔒 Mot de passe administrateur : ')
    second = getpass.getpass('  🔒 Confirmer le mot de passe : ')
    if first != second:
        raise AdminError('Les mots de passe ne correspondent pas. Aucune modification.')
    if len(first) < 14:
        raise AdminError('Mot de passe trop court : au moins 14 caractères.')
    return first


def _require_authorization() -> None:
    from autokom_security import configured, authorize, SecurityError
    if not configured():
        raise AdminError('Passphrase AUTOKOM absente : initialiser avec sudo autokom auth setup.')
    try:
        authorize('administration Django')
    except SecurityError as exc:
        raise AdminError(str(exc)) from exc


def _execute(action: str, payload: dict) -> dict:
    # Import différé : garder la CLI disponible même pendant une panne Django.
    from autokom_app_manager import (BACKEND, VENV, USER, AppError,
                                      _compose_env, django_env, load_state)

    if os.geteuid() != 0:
        raise AdminError('Gestion des administrateurs : utiliser sudo.')
    state = load_state()
    script = BACKEND / 'helpers' / 'admin_console.py'
    if not script.is_file():
        raise AdminError('Module admin absent du backend : exécuter autokom update.')
    if state['runtime'] == 'systemd':
        python = VENV / 'bin' / 'python'
        if not python.is_file():
            raise AdminError('Python du backend absent : exécuter autokom app setup.')
        env = django_env()
        env['DJANGO_SETTINGS_MODULE'] = 'config.settings'
        command = ['runuser', '-u', USER, '--', str(python), str(script), action]
    elif state['runtime'] == 'docker':
        env = _compose_env(state)
        command = ['docker', 'compose', '-f', str(BACKEND / 'docker-compose.autokom.yml'),
                   'exec', '-T', 'web', 'python', '/app/helpers/admin_console.py', action]
    else:
        raise AdminError('Runtime backend non pris en charge.')
    try:
        proc = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, cwd=BACKEND,
                              env=env, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdminError('Exécution backend indisponible ; vérifier autokom app status.') from exc
    # La sortie du helper doit être un seul JSON. Ne jamais propager un traceback
    # brut : il pourrait contenir des chemins ou des informations confidentielles.
    try:
        response = json.loads(proc.stdout.strip())
        if not isinstance(response, dict) or not isinstance(response.get('ok'), bool):
            raise ValueError('réponse incorrecte')
    except (ValueError, json.JSONDecodeError) as exc:
        raise AdminError('Réponse Django invalide ; consulter les journaux du backend.') from exc
    if proc.returncode or not response['ok']:
        message = response.get('message', 'Opération refusée par Django.')
        raise AdminError(str(message)[:350])
    return response


def _menu() -> str | None:
    if not _interactive():
        raise AdminError('Choisir une action : create, list, reset-password, disable ou enable.')
    print('  ◈ AUTOKOM · ADMINISTRATION DJANGO')
    print('  1. Créer un administrateur')
    print('  2. Lister les administrateurs')
    print('  3. Changer un mot de passe')
    print('  4. Désactiver un administrateur')
    print('  5. Réactiver un administrateur')
    print('  0. Annuler')
    return {'1': 'create', '2': 'list', '3': 'reset-password',
            '4': 'disable', '5': 'enable', '0': None}.get(input('  Choix : ').strip(), None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='autokom app admin',
        description='Administrateurs Django du backend (pas les comptes clients)',
    )
    sub = parser.add_subparsers(dest='action', required=False)
    for name, help_text in [
        ('create', 'Créer un administrateur du Django Admin'),
        ('list', 'Lister les comptes administrateurs'),
        ('reset-password', 'Réinitialiser un mot de passe administrateur'),
        ('disable', 'Désactiver un administrateur (sans suppression)'),
        ('enable', 'Réactiver un administrateur'),
    ]:
        p = sub.add_parser(name, help=help_text)
        if name != 'list':
            p.add_argument('--email', help='Adresse de l’administrateur (aucun mot de passe en argument)')
        if name == 'create':
            p.add_argument('--first-name', default='', help='Prénom')
            p.add_argument('--last-name', default='', help='Nom')
    args = parser.parse_args(argv)
    action = args.action or _menu()
    if action is None:
        return 0
    if os.geteuid() != 0:
        raise AdminError('Gestion des administrateurs : utiliser sudo.')
    payload = {}
    if action != 'list':
        payload['email'] = _email(getattr(args, 'email', None))
    if action == 'create':
        payload['first_name'] = getattr(args, 'first_name', '')
        payload['last_name'] = getattr(args, 'last_name', '')
    if action in ('create', 'reset-password'):
        # Autoriser avant de solliciter le mot de passe du nouvel administrateur.
        _require_authorization()
        payload['password'] = _password()
    elif action in ('disable', 'enable'):
        _require_authorization()
        if not _interactive():
            raise AdminError('Confirmation interactive obligatoire pour modifier un compte.')
        confirmation = input(f'  Confirmer {action} pour {payload["email"]} ? [o/N] : ').strip().lower()
        if confirmation not in ('o', 'oui'):
            print('  › Annulé. Aucun compte modifié.')
            return 0
    # list est lecture seule, mais réservé à root ; pas de passphrase additionnelle.
    response = _execute(action, payload)
    if action == 'list':
        print('  ◈ AUTOKOM · ADMINISTRATEURS')
        admins = response.get('admins', [])
        if not admins:
            print('  › Aucun administrateur créé.')
        for item in admins:
            status = 'actif' if item['active'] else 'désactivé'
            print(f'  {item["email"]} · {status} · superuser={"oui" if item["superuser"] else "non"}')
    else:
        print('  ✓ ' + response.get('message', 'Opération validée.'))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except AdminError as exc:
        print('  ✗ ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
