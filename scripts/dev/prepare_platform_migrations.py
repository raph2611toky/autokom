#!/usr/bin/env python3
"""Generate and review migration files on the development checkout (never production).

Django's makemigrations is a source-code operation, not a database schema update.
The resulting migration files must be reviewed, committed and then deployed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='AUTOKOM · Préparer les migrations Django sur Kali')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--python', default=None, help='Python du venv Django (si différent)')
    parser.add_argument('--apply', action='store_true', help='Générer les fichiers de migration (aucun SQL appliqué)')
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    if root == Path('/opt/autokom/source') or str(root).startswith('/opt/autokom/source/'):
        parser.error('Refus : générer les migrations dans le dépôt de développement, pas sur le serveur de production.')
    backend = root / 'platform/backend'
    manage = backend / 'manage.py'
    if not manage.is_file():
        parser.error(f'manage.py introuvable : {manage}')
    candidates = [backend / '.venv/bin/python', root / '.venv/bin/python']
    python = args.python or next((str(p) for p in candidates if p.is_file()), sys.executable)
    environment = os.environ.copy()
    # Allowed ONLY for migration file generation; never touch production settings.
    environment.setdefault('DEBUG', 'True')
    environment.setdefault('DJANGO_SECRET_KEY', 'autokom-local-migration-generator-not-for-production')
    environment.setdefault('DB_ENGINE', 'mysql')
    environment.setdefault('DB_PASSWORD', 'not-a-production-password')
    cmd = [python, str(manage), 'makemigrations', '--noinput']
    if not args.apply:
        cmd.extend(['--dry-run', '--verbosity', '2'])
    print('AUTOKOM · Migrations Django · ' + ('génération locale' if args.apply else 'simulation'), flush=True)
    print('Dossier :', backend, flush=True)
    result = subprocess.run(cmd, cwd=backend, env=environment, check=False)
    if result.returncode:
        print('Échec de makemigrations : vérifier les dépendances Django et les relations entre modèles.', file=sys.stderr)
        return result.returncode
    if args.apply:
        print('Fichiers générés. Vérifier git status, relire les migrations puis les committer sur main.')
        print('Aucune migration MySQL n’a été appliquée par cet outil.')
    else:
        print('Simulation terminée. --apply pour créer les fichiers de migrations dans le dépôt local.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
