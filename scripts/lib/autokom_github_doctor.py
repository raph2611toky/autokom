"""Diagnostic GitHub App AUTOKOM, local et sans divulgation de secrets.

Ne contacte pas GitHub et ne valide pas un secret auprès du fournisseur.
Exécution root uniquement : lit backend.env et frontend.env en lecture seule.
"""
from __future__ import annotations

import base64
import os
import re
from urllib.parse import urlsplit
from pathlib import Path

BACKEND_ENV = Path('/etc/autokom/secrets/backend.env')
FRONTEND_ENV = Path('/etc/autokom/secrets/frontend.env')


def callback_ok(value: str, expected_path: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and bool(url.hostname) and url.port in (None, 443) and \
            not url.username and not url.password and not url.query and not url.fragment and url.path == expected_path
    except (ValueError, TypeError):
        return False


def fernet_key_shape_ok(key: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(key.encode('ascii'))) == 32
    except (ValueError, UnicodeError):
        return False


def diagnose(backend: dict, frontend: dict) -> tuple[list[str], list[str], list[str]]:
    good, warnings, errors = [], [], []
    required = ('GITHUB_APP_CLIENT_ID', 'GITHUB_APP_CLIENT_SECRET', 'GITHUB_APP_SLUG',
                'GITHUB_APP_CALLBACK_URL', 'AUTOKOM_FIELD_ENCRYPTION_KEY')
    for name in required:
        if not backend.get(name):
            errors.append(name + ' : variable manquante.')
    if backend.get('GITHUB_APP_CLIENT_SECRET'):
        good.append('Client secret GitHub présent (non vérifié auprès du fournisseur).')
    if backend.get('GITHUB_APP_SLUG') and not re.fullmatch(r'[A-Za-z0-9-]+', backend['GITHUB_APP_SLUG']):
        errors.append('GITHUB_APP_SLUG invalide.')
    callbacks = {
        'dépôts': (backend.get('GITHUB_APP_CALLBACK_URL', ''), '/api/projects/git/github/callback/'),
        'comptes': (backend.get('GITHUB_AUTH_CALLBACK_URL', '') or '', '/api/auth/github/callback/'),
    }
    project_url = callbacks['dépôts'][0]
    if not callbacks['comptes'][0] and callback_ok(project_url, callbacks['dépôts'][1]):
        origin = urlsplit(project_url)
        callbacks['comptes'] = (f'{origin.scheme}://{origin.netloc}/api/auth/github/callback/', callbacks['comptes'][1])
    for name, (url, path) in callbacks.items():
        if not callback_ok(url, path):
            errors.append(f'Callback GitHub {name} invalide ou manquant.')
        else:
            good.append(f'Callback {name} à enregistrer dans GitHub App : {url}')
    if backend.get('AUTOKOM_FIELD_ENCRYPTION_KEY') and not fernet_key_shape_ok(backend['AUTOKOM_FIELD_ENCRYPTION_KEY']):
        errors.append('AUTOKOM_FIELD_ENCRYPTION_KEY : format Fernet invalide.')
    elif backend.get('AUTOKOM_FIELD_ENCRYPTION_KEY'):
        good.append('Clé Fernet : format attendu (déchiffrement des jetons stockés non vérifié).')
    if frontend.get('VITE_AUTOKOM_GITHUB_ENABLED', '').lower() != 'true':
        warnings.append('VITE_AUTOKOM_GITHUB_ENABLED n’est pas activée.')
    if frontend.get('VITE_AUTOKOM_API_ENABLED', '').lower() != 'true':
        warnings.append('VITE_AUTOKOM_API_ENABLED n’est pas activée.')
    if frontend.get('VITE_AUTOKOM_FRONTEND_ONLY', '').lower() != 'false':
        warnings.append('VITE_AUTOKOM_FRONTEND_ONLY doit être false pour la production.')
    if frontend.get('FRONTEND_URL') and backend.get('FRONTEND_URL') and \
        frontend['FRONTEND_URL'].rstrip('/') != backend['FRONTEND_URL'].rstrip('/'):
        errors.append('FRONTEND_URL : divergence entre backend et frontend.')
    if frontend.get('BACKEND_URL') and callback_ok(project_url, callbacks['dépôts'][1]):
        from urllib.parse import urlsplit as split
        if split(frontend['BACKEND_URL']).netloc != split(project_url).netloc:
            warnings.append('BACKEND_URL diffère de l’hôte du callback GitHub.')
    if not backend.get('GITHUB_WEBHOOK_SECRET'):
        warnings.append('Webhook GitHub non configuré (nécessaire pour le déploiement sur push).')
    warnings.append('L’enregistrement effectif des deux callbacks et les permissions GitHub ne peuvent être vérifiés localement.')
    return good, warnings, errors


def main():
    if os.geteuid() != 0:
        print('  ✗ Diagnostic des secrets réservé à root : utiliser sudo.')
        return 1
    from autokom_env_manager import read_env
    backend = read_env(BACKEND_ENV)
    frontend = read_env(FRONTEND_ENV)
    good, warnings, errors = diagnose(backend, frontend)
    print('  ◈ AUTOKOM · GitHub App · Diagnostic local (aucun secret affiché)')
    for item in good:
        print('  ✓ ' + item)
    for item in warnings:
        print('  › ' + item)
    for item in errors:
        print('  ✗ ' + item)
    return 1 if errors else 0
