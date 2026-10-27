#!/usr/bin/env python3
"""Affichage centralise des logos CLI AUTOKOM fournis par le projet.

Les logos sont visibles sur terminal interactif, pas dans les journaux CI.
AUTOKOM_BANNERS=off  : aucun logo
AUTOKOM_BANNERS=auto : une fois / outil / processus (defaut)
AUTOKOM_BANNERS=always : un logo a chaque invocation
NO_COLOR=1 / --no-color : logos monochromes
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = frozenset({'jenkins', 'prometheus', 'grafana', 'kibana', 'wazuh'})
_SEEN = set()


def render(tool: str, *, force: bool = False, no_color: bool = False) -> bool:
    """Affiche un logo au plus une fois par outil, sauf mode explicitement always.

    Un echec de rendu est non bloquant pour l'installation; jamais de commandes
    shell ni de dependances installees ici.
    """
    if tool not in _TOOLS:
        raise ValueError('Outil AUTOKOM inconnu : ' + tool)
    policy = os.environ.get('AUTOKOM_BANNERS', 'auto').strip().lower()
    if policy in ('off', '0', 'false', 'no'):
        return False
    if policy not in ('auto', 'always'):
        policy = 'auto'
    if tool in _SEEN and policy != 'always':
        return False
    if not sys.stdout.isatty() and not force:
        return False

    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    if width < 64 and not force:
        # Ne pas couper le dessin quand la fenetre SSH est etroite.
        print(f'  AUTOKOM / {tool.upper()}  (agrandir le terminal pour le logo)', flush=True)
        _SEEN.add(tool)
        return True

    path = _ROOT / 'scripts' / 'banners' / f'{tool}.py'
    if not path.is_file():
        print(f'  AUTOKOM / {tool.upper()}  (banniere absente)', flush=True)
        return False
    cmd = [sys.executable, str(path)]
    if no_color or os.environ.get('NO_COLOR') is not None or not sys.stdout.isatty():
        cmd.append('--no-color')
    if tool == 'wazuh':
        cmd.extend(['--width', str(min(60, max(24, width - 4)))])
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=12, check=False)
        if proc.returncode:
            return False
        print('\n' + proc.stdout.rstrip('\n') + '\n', flush=True)
        _SEEN.add(tool)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='AUTOKOM : bannieres pour outils DevSecOps')
    parser.add_argument('tool', choices=sorted(_TOOLS))
    parser.add_argument('--force', action='store_true', help='Afficher aussi vers stdout non-TTY')
    parser.add_argument('--no-color', action='store_true')
    args = parser.parse_args(argv)
    return 0 if render(args.tool, force=args.force, no_color=args.no_color) else 0


if __name__ == '__main__':
    raise SystemExit(main())
