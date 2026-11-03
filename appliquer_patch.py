#!/usr/bin/env python3
"""Install a non-destructive AUTOKOM Core/Platform patch.

Default is preview. --apply adds new files and makes guarded edits to existing
CLI/settings without touching unrelated scripts, frontend files, or databases.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent
NEW = [
    'scripts/lib/autokom_private_update.py',
    'scripts/lib/autokom_app_manager.py',
    'platform/backend/config/health.py',
    'platform/backend/config/swagger/__init__.py',
    'platform/backend/config/swagger/health.py',
    'platform/backend/Dockerfile.autokom',
    'platform/backend/docker-compose.autokom.yml',
    'platform/backend/.dockerignore',
    'pipelines/autokom-backend/Jenkinsfile',
    'docs/AUTOKOM-PLATFORM-SERVICE-PRIVATE-UPDATE.md',
    'tests/test_autokom_platform_lifecycle.py',
]


class PatchError(Exception):
    pass


def once(text, old, new, name):
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise PatchError(f'{name}: structure inattendue ({count} occurrence(s)). Refus de modifier aveuglément.')
    return text.replace(old, new, 1)


def patch_cli(text):
    text = once(text,
                "    subcommand('update', help='Mise à jour Git fast-forward uniquement (explicite)',\n               epilog='Exemple : sudo autokom update')",
                "    subcommand('app', help='Service Django AUTOKOM : setup, deploy, status, logs')\n"
                "    up = subcommand('update', help='Mise à jour privée via code GitHub (fast-forward uniquement)',\n"
                "                    epilog='Exemple : sudo autokom update')\n"
                "    up.add_argument('--check', action='store_true', help='Vérifier localement sans accéder à GitHub')",
                'CLI: parser app/update')
    text = once(text,
                "        mutating=(cmd in ('update','configure') or",
                "        mutating=(cmd in ('update','configure') or\n"
                "            (cmd=='app' and len(arguments)>1 and arguments[1] in ('setup','deploy') and '--check' not in arguments) or",
                'CLI: protected mutations')
    text = once(text,
                "    # Delegate BEFORE argparse's generic tools/files grammar; all nested -h",
                "    if arguments and arguments[0] == 'app':\n"
                "        from autokom_app_manager import main as app_main, AppError\n"
                "        try:\n"
                "            return app_main(arguments[1:])\n"
                "        except AppError as exc:\n"
                "            raise CLIError(str(exc)) from exc\n"
                "    # Delegate BEFORE argparse's generic tools/files grammar; all nested -h",
                'CLI: app dispatcher')
    before = "    if a.cmd == 'update':\n"
    after = "    if a.cmd=='grafana':\n"
    if "from autokom_private_update import update as private_update" not in text:
        start = text.find(before)
        end = text.find(after, start)
        if start == -1 or end == -1 or end <= start:
            raise PatchError('CLI: branche update introuvable ; aucune modification.')
        text = (text[:start] + "    if a.cmd == 'update':\n"
                "        from autokom_private_update import update as private_update, UpdateError\n"
                "        if os.geteuid() != 0:\n"
                "            raise CLIError('Utiliser sudo autokom update')\n"
                "        try:\n"
                "            return private_update(ROOT, check=a.check)\n"
                "        except UpdateError as exc:\n"
                "            raise CLIError(str(exc)) from exc\n" + text[end:])
    return text


def patch_settings(text):
    text = once(text,
                "STATIC_ROOT = BASE_DIR / 'staticfiles'",
                "STATIC_ROOT = Path(os.getenv('AUTOKOM_STATIC_ROOT', str(BASE_DIR / 'staticfiles')))",
                'settings: STATIC_ROOT')
    text = once(text,
                "MEDIA_ROOT = BASE_DIR / 'media'",
                "MEDIA_ROOT = Path(os.getenv('AUTOKOM_MEDIA_ROOT', str(BASE_DIR / 'media')))",
                'settings: MEDIA_ROOT')
    text = once(text,
                "ROOT_URLCONF = 'config.urls'",
                "if env_bool('AUTOKOM_SERVE_STATIC', False):\n"
                "    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')\n"
                "ROOT_URLCONF = 'config.urls'",
                'settings: whitenoise for Docker')
    text = once(text,
                "SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'",
                "SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'\n"
                "SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))\n"
                "SECURE_HSTS_INCLUDE_SUBDOMAINS = False\n"
                "SECURE_HSTS_PRELOAD = False",
                'settings: HTTPS HSTS')
    return text


def patch_urls(text):
    text = once(text,
                'from config.views import SwaggerLoginPageAPIView, SwaggerLoginSubmitAPIView, SwaggerLogoutAPIView',
                'from config.views import SwaggerLoginPageAPIView, SwaggerLoginSubmitAPIView, SwaggerLogoutAPIView\n'
                'from config.health import PlatformHealthAPIView',
                'urls: health import')
    text = once(text,
                "client_api_patterns = [\n",
                "client_api_patterns = [\n    path('api/health/', PlatformHealthAPIView.as_view(), name='platform-health'),\n",
                'urls: health route')
    return text


def patch_requirements(text):
    for req in ('gunicorn>=22,<24', 'whitenoise>=6.8,<7'):
        if not any(line.strip().lower().startswith(req.split('>')[0]) for line in text.splitlines()):
            text = text.rstrip() + '\n' + req + '\n'
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, type=Path, help='Racine du dépôt AUTOKOM')
    p.add_argument('--apply', action='store_true', help='Appliquer le patch (sans sauvegarde automatique)')
    args = p.parse_args()
    repo = args.repo.resolve()
    if not (repo / 'autokom').is_file() or not (repo / 'platform/backend/manage.py').is_file():
        raise PatchError('Cible invalide : choisir la racine du dépôt AUTOKOM, pas platform/.')
    edits = {
        'scripts/lib/autokom_cli.py': patch_cli,
        'platform/backend/config/settings.py': patch_settings,
        'platform/backend/config/urls.py': patch_urls,
        'platform/backend/requirements.txt': patch_requirements,
    }
    old_new = {}
    for target, fn in edits.items():
        src = repo / target
        if not src.is_file():
            raise PatchError(f'{target} absent. Restaurer AUTOKOM Core avant installation du patch.')
        original = src.read_text(encoding='utf-8')
        updated = fn(original)
        compile(updated, str(src), 'exec') if target.endswith('.py') else None
        old_new[src] = updated
    for name in NEW:
        content = (HERE / name).read_bytes()
        path = repo / name
        if path.exists() and path.read_bytes() != content:
            raise PatchError(f'Fichier déjà présent avec contenu différent : {name}. Refus de l’écraser.')
    print('AUTOKOM · Mise à jour du socle privé et du service backend')
    for file, content in old_new.items():
        print(('MODIFIER ' if file.read_text(encoding='utf-8') != content else 'INCHANGÉ') + str(file.relative_to(repo)))
    for name in NEW:
        print(('AJOUTER ' if not (repo/name).exists() else 'INCHANGÉ') + name)
    if not args.apply:
        print('\nSimulation uniquement. Ajouter --apply pour modifier les fichiers.')
        return 0
    for path, contents in old_new.items():
        if path.read_text(encoding='utf-8') != contents:
            mode = stat.S_IMODE(path.stat().st_mode)
            tmp = path.with_name(path.name + '.autokom-new')
            tmp.write_text(contents, encoding='utf-8')
            os.chmod(tmp, mode)
            os.replace(tmp, path)
    for name in NEW:
        destination = repo / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes((HERE/name).read_bytes())
    print('\nPatch appliqué. Vérifier git diff --stat, puis les commandes autokom app -h.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (PatchError, OSError, SyntaxError) as exc:
        print('ERREUR : ' + str(exc), file=sys.stderr)
        sys.exit(1)
