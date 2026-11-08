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
    project_url = backend.get('GITHUB_APP_CALLBACK_URL', '')
    frontend_origin = backend.get('FRONTEND_URL', '').rstrip('/') or frontend.get('FRONTEND_URL', '').rstrip('/')
    expected_login_url = frontend_origin + '/auth/github/callback'
    legacy_login_url = backend.get('GITHUB_AUTH_CALLBACK_URL', '')
    if not callback_ok(project_url, '/api/projects/git/github/callback/'):
        errors.append('Callback GitHub des dépôts invalide.')
    else:
        good.append(f'Callback des dépôts (inchangé) : {project_url}')
    if not callback_ok(expected_login_url, '/auth/github/callback'):
        errors.append('FRONTEND_URL invalide pour le callback GitHub client.')
    else:
        good.append(f'NOUVEAU callback connexion à déclarer dans GitHub App : {expected_login_url}')
    if legacy_login_url:
        if (not callback_ok(legacy_login_url, '/api/auth/github/callback/') and
                not callback_ok(legacy_login_url, '/auth/github/callback')):
            errors.append('GITHUB_AUTH_CALLBACK_URL : callback client invalide.')
        elif legacy_login_url != expected_login_url:
            warnings.append('Ancien callback de connexion détecté : le backend utilise automatiquement le frontend.')
    if backend.get('FRONTEND_URL') and frontend.get('FRONTEND_URL') and \
            backend['FRONTEND_URL'].rstrip('/') != frontend['FRONTEND_URL'].rstrip('/'):
        errors.append('FRONTEND_URL : divergence entre backend et frontend.')
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
    if frontend.get('BACKEND_URL') and callback_ok(project_url, '/api/projects/git/github/callback/'):
        from urllib.parse import urlsplit as split
        if split(frontend['BACKEND_URL']).netloc != split(project_url).netloc:
            warnings.append('BACKEND_URL diffère de l’hôte du callback GitHub.')
    if not backend.get('GITHUB_WEBHOOK_SECRET'):
        warnings.append('Webhook GitHub non configuré (nécessaire pour le déploiement sur push).')
    warnings.append('Ajouter le nouveau callback frontend dans GitHub App > General ; désactiver le wildcard. L’enregistrement distant ne peut pas être validé localement.')
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
