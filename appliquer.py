#!/usr/bin/env python3
"""AUTOKOM: restore ignored Django secrets *source code* + 3-line live progress.

Dry-run by default. No backups, no deletes, no service changes and no secrets read.
The expected original app-manager is stored as a 3-way merge base; conflicts fail.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MANAGER = Path('scripts/lib/autokom_app_manager.py')
APP = Path('platform/backend/apps/projects/secrets')
MARKER = '!platform/backend/apps/projects/secrets/'


def merge_file(local: bytes, base: bytes, desired: bytes) -> bytes:
    if local == desired:
        return local
    if local == base:
        return desired
    with tempfile.TemporaryDirectory(prefix='autokom-merge-') as directory:
        paths = [Path(directory, p) for p in ('current', 'base', 'desired')]
        for file, content in zip(paths, (local, base, desired)):
            file.write_bytes(content)
        proc = subprocess.run(['git', 'merge-file', '-p', *map(str, paths)],
                              capture_output=True, check=False)
        if proc.returncode or b'<<<<<<<' in proc.stdout or b'>>>>>>>' in proc.stdout:
            raise RuntimeError('Conflit dans autokom_app_manager.py : aucune modification effectuée.')
        return proc.stdout


def collect(repo: Path) -> dict[Path, bytes]:
    if not (repo / 'autokom').is_file() or not (repo / 'platform/backend/manage.py').is_file():
        raise RuntimeError('Ce répertoire ne ressemble pas à la racine du dépôt AUTOKOM.')
    changes: dict[Path, bytes] = {}
    gitignore = repo / '.gitignore'
    if not gitignore.is_file() or gitignore.is_symlink():
        raise RuntimeError('.gitignore à la racine manquant ou lien symbolique.')
    old_ignore = gitignore.read_text(encoding='utf-8')
    if 'secrets/' not in old_ignore.splitlines() and MARKER not in old_ignore.splitlines():
        raise RuntimeError('La règle secrets/ attendue est absente : vérifier le .gitignore avant correction.')
    if MARKER not in old_ignore.splitlines():
        # Unignore *directory* only. Ignore rules for .env, *.pem, *.key stay active.
        new_ignore = old_ignore.rstrip('\n') + (
            '\n\n# Package Python Django versionné (aucune donnée secrète ici)\n'
            + MARKER + '\n'
        )
        changes[gitignore] = new_ignore.encode('utf-8')
    dst_manager = repo / MANAGER
    if not dst_manager.is_file() or dst_manager.is_symlink():
        raise RuntimeError('Gestionnaire AUTOKOM manquant (scripts/lib/autokom_app_manager.py).')
    new_manager = merge_file(
        dst_manager.read_bytes(),
        (HERE/'base'/MANAGER).read_bytes(),
        (HERE/MANAGER).read_bytes(),
    )
    if new_manager != dst_manager.read_bytes():
        changes[dst_manager] = new_manager
    for pkg_file in sorted((HERE/APP).rglob('*.py')):
        relative = pkg_file.relative_to(HERE)
        target = repo / relative
        if any(part.startswith('.') for part in relative.parts) or '__pycache__' in relative.parts:
            raise RuntimeError('Chemin package non autorisé : ' + str(relative))
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != repo.parent):
            raise RuntimeError('Lien symbolique interdit : '+str(relative))
        if target.exists():
            # Respect local source code: never overwrite even when it differs.
            continue
        changes[target] = pkg_file.read_bytes()
    for relative in (Path('tests/test_autokom_secrets_live_ui.py'),
                     Path('docs/AUTOKOM-SECRETS-SOURCE-ET-LIVE-LOGS.md')):
        target = repo / relative
        if target.is_symlink():
            raise RuntimeError('Lien symbolique interdit : ' + str(relative))
        if not target.exists():
            changes[target] = (HERE / relative).read_bytes()
        elif target.read_bytes() != (HERE / relative).read_bytes():
            raise RuntimeError('Fichier existant différent, refus de remplacer : ' + str(relative))
    return changes


def apply(changes: dict[Path, bytes]):
    for path, data in changes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        try:
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Corriger package Django secrets et loader sans modifier la production')
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    changes = collect(root)
    if not changes:
        print('Rien à modifier : correctif déjà appliqué.')
        return 0
    for path in changes:
        print(f"  {'MODIFIER' if path.exists() else 'AJOUTER'} {path.relative_to(root)}")
    if args.apply:
        apply(changes)
        print('\nCorrectif appliqué. Vérifier git diff et suivre le package Django avant de publier.')
    else:
        print('\nSimulation uniquement. Pour appliquer, ajouter --apply.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print('ERREUR : ' + str(exc), file=sys.stderr)
        sys.exit(1)
