#!/usr/bin/env python3
"""Affichage centralise des logos CLI AUTOKOM fournis par le projet.

Les logos complets sont STRICTEMENT reserves aux PREMIERES installations
reelles de services absents, dans un terminal interactif. Jamais en audit,
--check, post-configuration, reparation, tests ou re-execution.
AUTOKOM_BANNERS=off : aucun grand logo. NO_COLOR=1 : monochrome.
Le rendu explicite via --preview sert uniquement au controle manuel de l'art.
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


def first_install(mode: str, installed: bool) -> bool:
    """Un service absent, lors d'une vraie application seulement."""
    return mode == 'apply' and not installed


def section(title: str, detail: str = '') -> None:
    """En-tete compact encadre, utilise pour toutes les phases hors installation initiale."""
    width = min(max(shutil.get_terminal_size((80, 24)).columns - 6, 44), 68)
    inner = width - 2
    def crop(s):
        return s[:max(0, inner - 3)]
    print('\n  ╭' + '─' * inner + '╮')
    for msg in ('◈ AUTOKOM  /  ' + title.upper(), detail):
        if not msg:
            continue
        body = crop(msg)
        print('  │ ' + body + ' ' * (inner - 2 - len(body)) + ' │')
    print('  ╰' + '─' * inner + '╯', flush=True)


def render(tool: str, *, initial_install: bool = False, force: bool = False, no_color: bool = False) -> bool:
    """Grand logo uniquement si le moteur confirme une premiere installation.

    `force` permet l'apercu explicite (CLI manuel), jamais un audit automatique.
    Aucun effet de bord hormis l'affichage.
    """
    if tool not in _TOOLS:
        raise ValueError('Outil AUTOKOM inconnu : ' + tool)
    if not (initial_install or force):
        return False
    policy = os.environ.get('AUTOKOM_BANNERS', 'auto').strip().lower()
    if policy in ('off', '0', 'false', 'no'):
        return False
    if policy not in ('auto', 'always'):
        policy = 'auto'
    if tool in _SEEN:
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
    parser.add_argument('--preview', action='store_true', help='Apercu manuel du grand logo (jamais automatique)')
    parser.add_argument('--force', action='store_true', help='Alias de --preview (controle manuel)')
    parser.add_argument('--no-color', action='store_true')
    args = parser.parse_args(argv)
    return 0 if render(args.tool, force=(args.preview or args.force), no_color=args.no_color) else 0


if __name__ == '__main__':
    raise SystemExit(main())
