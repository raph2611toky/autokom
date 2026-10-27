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


# Compact AUTOKOM frames: service-specific light colors, no ANSI in redirected
# logs or when NO_COLOR is present. Full service logos remain first-install only.
_FRAME_COLORS = {
    'autokom': '38;2;125;211;252',       # bleu ciel clair
    'foundation': '38;2;134;239;172',    # vert clair
    'jenkins': '38;2;125;211;252',       # bleu clair
    'prometheus': '38;2;255;211;120',    # jaune chaud
    'grafana': '38;2;255;187;120',       # orange clair
    'observability': '38;2;253;224;71', # jaune clair
    'security': '38;2;253;224;71',      # jaune clair
}


def _section_tone(title: str) -> str:
    normalized = title.casefold()
    for needle, tone in (
        ('grafana', 'grafana'),
        ('prometheus', 'prometheus'),
        ('jenkins', 'jenkins'),
        ('observab', 'observability'),
        ('sécurit', 'security'),
        ('security', 'security'),
        ('réseau', 'security'),
        ('fondation', 'foundation'),
        ('installation', 'foundation'),
    ):
        if needle in normalized:
            return tone
    return 'autokom'


def _color_enabled() -> bool:
    return (sys.stdout.isatty() and os.environ.get('NO_COLOR') is None
            and os.environ.get('TERM', '') not in ('', 'dumb'))


def section(title: str, detail: str = '', *, tone: str | None = None) -> None:
    """Compact colored frame for audits/menus; never prints large logos.

    Tones may be explicitly set when a generic title covers a known service.
    Styling is applied *after* padding to avoid corrupting box widths.
    """
    theme = tone or _section_tone(title)
    color = _FRAME_COLORS.get(theme, _FRAME_COLORS['autokom'])
    enabled = _color_enabled()
    fg = f'\033[{color}m' if enabled else ''
    bold = '\033[1m' if enabled else ''
    muted = '\033[2m' if enabled else ''
    reset = '\033[0m' if enabled else ''
    width = min(max(shutil.get_terminal_size((80, 24)).columns - 6, 44), 68)
    inner = width - 2
    heading = '◈ AUTOKOM  /  ' + title.upper()
    print('\n  ' + fg + '╭' + '─' * inner + '╮' + reset)
    for index, message in enumerate((heading, detail)):
        if not message:
            continue
        body = message[:max(0, inner - 3)]
        padding = ' ' * max(0, inner - 2 - len(body))
        style = (bold + fg) if index == 0 else muted
        print('  ' + fg + '│' + reset + ' ' + style + body + reset + padding + ' ' + fg + '│' + reset)
    print('  ' + fg + '╰' + '─' * inner + '╯' + reset, flush=True)


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
