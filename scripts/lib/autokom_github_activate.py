"""Activation coordonnée du parcours GitHub App (Django + SPA).

Une seule commande sur GCP, pas de secrets dans stdout, pas de modification des
callbacks GitHub distants (opération administrateur requise dans GitHub).
"""
from __future__ import annotations

import argparse
from types import SimpleNamespace
from urllib.parse import urlsplit

from cli_colors import pretty_print as say
from autokom_env_manager import read_env, write_env, backup, _validate, require_root
from autokom_github_doctor import diagnose
from autokom_app_manager import ENV_FILE as BACKEND_ENV, AppError
from autokom_frontend_manager import SECRETS as FRONTEND_ENV

CALLBACK_PATH = '/auth/github/callback'


def activate(*, apply: bool) -> int:
    require_root()
    backend = read_env(BACKEND_ENV)
    frontend = read_env(FRONTEND_ENV)
    good, warnings, errors = diagnose(backend, frontend)
    if not backend or not frontend:
        errors.append('Backend ou frontend non configuré : utiliser les setup existants avant GitHub.')
    expected = backend.get('FRONTEND_URL', '').rstrip('/') + CALLBACK_PATH
    if not expected.startswith('https://'):
        errors.append('FRONTEND_URL du backend doit être HTTPS.')
    new_frontend = dict(frontend)
    new_frontend.update({
        'VITE_AUTOKOM_FRONTEND_ONLY': 'false',
        'VITE_AUTOKOM_API_ENABLED': 'true',
        'VITE_AUTOKOM_GITHUB_ENABLED': 'true',
        'VITE_AUTOKOM_AUTH_MODE': 'cookie',
    })
    try:
        _validate('frontend', new_frontend)
    except Exception as exc:
        errors.append('Configuration frontend non valide : ' + str(exc))
    say('  ◈ AUTOKOM · GITHUB · AUTHENTIFICATION FRONTEND')
    say('  › GitHub App :', backend.get('GITHUB_APP_SLUG', 'non configurée'))
    say('  › Callback client :', expected)
    say('  › Callback dépôts :', backend.get('GITHUB_APP_CALLBACK_URL', 'non configuré'))
    say('  › Mode : cookies HttpOnly + protection CSRF + PKCE + state navigateur')
    for item in errors:
        say('  ✗ ' + item)
    if errors:
        raise AppError('Activation GitHub bloquée : corriger les prérequis listés.')
    say('  ! GitHub > Developer settings > GitHub Apps > General : ajouter le callback client')
    say('  ! ' + expected + ' (correspondance exacte, wildcard désactivé).')
    say('  ! Permissions GitHub App : accès lecture à l’adresse e-mail utilisateur.')
    if not apply:
        say('  ✓ Prévisualisation réussie : aucune configuration modifiée.')
        return 0
    from autokom_app_manager import deploy as backend_deploy
    from autokom_frontend_manager import deploy as frontend_deploy
    say('  › Backend : tests DevSecOps, migrations et mise à jour Django')
    backend_deploy(SimpleNamespace(check=False))
    if frontend != new_frontend:
        backup(FRONTEND_ENV)
        write_env(FRONTEND_ENV, new_frontend)
    try:
        say('  › Frontend : tests, scanners DevSecOps, build React et publication')
        frontend_deploy(SimpleNamespace(check=False, apply=True))
    except Exception:
        if frontend != new_frontend:
            # Pas de demi-activation : ancienne release conservée par frontend_deploy.
            backup(FRONTEND_ENV)
            write_env(FRONTEND_ENV, frontend)
            say('  ! Paramètres frontend précédents restaurés après l’échec.')
        raise
    say('  ✓ Authentification GitHub activée côté AUTOKOM.')
    say('  › Vérifier : https://' + urlsplit(expected).netloc + '/login')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='autokom app github',
        description='Activation contrôlée de la connexion et inscription GitHub sur le frontend')
    sub = parser.add_subparsers(dest='action', required=True)
    activation = sub.add_parser('activate', help='Activer OAuth avec redirection directe GitHub → React')
    modes = activation.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true', help='Audit sans aucune modification')
    modes.add_argument('--apply', action='store_true', help='Déployer backend et frontend')
    args = parser.parse_args(argv)
    return activate(apply=args.apply)


if __name__ == '__main__':
    raise SystemExit(main())
