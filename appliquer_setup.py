#!/usr/bin/env python3
"""AUTOKOM - Install incremental backend setup wizard patch (default preview).

Uses a three-way merge for existing files; refuses conflicts instead of
replacing local customizations. Never invokes system services or MySQL.
"""
from __future__ import annotations
import argparse
import difflib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
CHANGED = [
    'scripts/lib/autokom_app_manager.py',
    'platform/backend/docker-compose.autokom.yml',
]
ADDED = [
    'ansible/playbooks/platform-backend.yml',
    'tests/test_autokom_setup_wizard.py',
    'docs/AUTOKOM-APP-SETUP-INTERACTIF.md',
]

class PatchError(Exception):
    pass


def merged(repo: Path, relative: str) -> tuple[str, bytes | None]:
    destination = repo / relative
    source = HERE / relative
    base = HERE / 'base' / relative
    if not destination.is_file() or not base.is_file() or not source.is_file():
        raise PatchError(f'Fichiers nécessaires manquants pour {relative}.')
    if destination.is_symlink():
        raise PatchError(f'Lien symbolique refusé : {relative}.')
    original = destination.read_bytes()
    desired = source.read_bytes()
    old = base.read_bytes()
    if original == desired:
        return 'INCHANGÉ', None
    if original == old:
        return 'REMPLACER VERSION RECONNUE', desired
    # Recognize the already-applied secure-env whitelist extension distributed
    # with an earlier patch; never demand that users discard their changes.
    bases = [old]
    if relative == 'scripts/lib/autokom_app_manager.py':
        variant = HERE / 'base/variants/autokom_app_manager_with_secrets.py'
        if variant.is_file():
            bases.append(variant.read_bytes())
    if original in bases:
        return 'REMPLACER VERSION RECONNUE', desired
    for candidate in bases:
        with tempfile.TemporaryDirectory() as d:
            paths = [Path(d)/name for name in ('local', 'base', 'update')]
            for file, blob in zip(paths, (original, candidate, desired)):
                file.write_bytes(blob)
            proc = subprocess.run(['git', 'merge-file', '-p', *map(str, paths)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode != 0 or b'<<<<<<<' in proc.stdout or b'>>>>>>>' in proc.stdout:
                continue
            if proc.stdout == original:
                return 'INCHANGÉ (adapté)', None
            return 'FUSION NON DESTRUCTIVE', proc.stdout
    raise PatchError(f'Conflit avec tes adaptations locales : {relative}. '
                     'Aucune modification appliquée, fusion manuelle nécessaire.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True, help='Racine Git du dépôt AUTOKOM')
    parser.add_argument('--apply', action='store_true', help='Appliquer après vérification')
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo/'autokom').is_file() or not (repo/'platform/backend/manage.py').is_file():
        raise PatchError('Racine AUTOKOM invalide.')
    changes = {}
    for relative in CHANGED:
        label, content = merged(repo, relative)
        print(f'{label}: {relative}')
        if content is not None:
            changes[relative] = content
    for relative in ADDED:
        path = repo/relative
        data = (HERE/relative).read_bytes()
        if path.is_symlink():
            raise PatchError(f'Lien symbolique refusé : {relative}.')
        if path.exists() and path.read_bytes() != data:
            raise PatchError(f'Fichier déjà existant différent : {relative}. '
                             'Aucune suppression ni écrasement automatique.')
        print(('INCHANGÉ' if path.exists() else 'AJOUTER')+': '+relative)
        if not path.exists():
            changes[relative] = data
    if not args.apply:
        print('\nSimulation uniquement. Réexécuter avec --apply après vérification.')
        return
    for relative, content in changes.items():
        path = repo/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement, no writes outside the explicit allowlist.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.autokom-setup-',
                                         delete=False) as handle:
            handle.write(content)
            staged = Path(handle.name)
        if path.exists():
            os.chmod(staged, path.stat().st_mode & 0o777)
        else:
            os.chmod(staged, 0o644)
        os.replace(staged, path)
    print('\nCorrectif appliqué. Vérifier git diff --stat avant le commit.')


if __name__ == '__main__':
    try:
        main()
    except (PatchError, OSError, subprocess.SubprocessError) as exc:
        print('ERREUR : '+str(exc), file=sys.stderr)
        sys.exit(1)
