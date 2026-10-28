#!/usr/bin/env python3
"""Tableaux CLI AUTOKOM : colorés en TTY, adaptatifs, sans dépendance.

Aucun contenu de cellule n'est exécuté. Les contrôles et séquences ANSI issus
notamment de variables d'environnement sont rendus visibles et inoffensifs.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shutil
import sys
import unicodedata


PALETTE = {
    'success': '\033[38;2;134;239;172m',
    'info': '\033[38;2;125;211;252m',
    'warning': '\033[38;2;255;211;120m',
    'error': '\033[38;2;255;140;140m',
    'muted': '\033[38;2;160;169;186m',
    'jenkins': '\033[38;2;125;211;252m',
    'prometheus': '\033[38;2;255;211;120m',
    'grafana': '\033[38;2;255;187;120m',
}
RESET = '\033[0m'


@dataclass(frozen=True)
class Cell:
    text: object
    tone: str = ''


def safe_text(value: object) -> str:
    """Make terminal output non-executable; retain all printable information."""
    if value is None:
        return '—'
    data = str(value)
    result = []
    for c in data:
        code = ord(c)
        if c == '\n': result.append('\\n')
        elif c == '\r': result.append('\\r')
        elif c == '\t': result.append('\\t')
        elif code < 32 or 0x7f <= code < 0xa0: result.append(f'\\x{code:02x}')
        else: result.append(c)
    return ''.join(result)


def visible_width(text: str) -> int:
    return sum(0 if unicodedata.combining(c) or c in ('\u200d', '\ufe0f')
               else 2 if unicodedata.east_asian_width(c) in ('W','F')
               else 1 for c in text)


def _chunks(text: str, width: int) -> list[str]:
    """Prefer word or path separators; never truncate content."""
    width = max(1, width)
    if not text:
        return ['']
    lines = []
    rest = text
    while rest:
        used = 0
        limit = 0
        for ch in rest:
            cells = visible_width(ch)
            if used + cells > width:
                break
            used += cells
            limit += 1
        if limit == len(rest):
            lines.append(rest)
            break
        # Avoid splitting ordinary words or absolute paths in mid-segment.
        # A separator too early in the line would create mostly empty rows.
        possible = [i + 1 for i, ch in enumerate(rest[:limit])
                    if ch in ('/', ' ', '-', '_', '.', ':')]
        possible = [i for i in possible if i >= max(2, limit // 2)]
        cutoff = possible[-1] if possible else limit
        lines.append(rest[:cutoff])
        rest = rest[cutoff:]
    return lines


def _enable_color(stream, enabled):
    if enabled is not None:
        return enabled
    return (getattr(stream, 'isatty', lambda: False)()
            and 'NO_COLOR' not in os.environ
            and os.environ.get('TERM','') not in ('', 'dumb'))


def render_table(headers, rows, *, width=None, color=None, stream=None, indent='  ') -> str:
    """Return table. With a narrow terminal, cells wrap rather than truncate."""
    output = stream if stream is not None else sys.stdout
    color = _enable_color(output, color)
    heads = [safe_text(h) for h in headers]
    n = len(heads)
    if not n:
        return ''
    normalized = []
    for row in rows:
        if len(row) != n:
            raise ValueError('Incompatible number of columns')
        normalized.append([c if isinstance(c, Cell) else Cell(c) for c in row])
    terminal = width or shutil.get_terminal_size(fallback=(94, 25)).columns
    # Borders: n cells each with two padding spaces, plus n+1 vertical rules.
    available = max(n * 6 + 1, terminal - visible_width(indent))
    budget = max(n * 3, available - (3 * n + 1))
    preferred = []
    for j in range(n):
        samples = [heads[j]] + [safe_text(row[j].text) for row in normalized]
        preferred.append(max(5, min(60, max(map(visible_width, samples), default=5))))
    widths = preferred[:]
    # Shrink the biggest column first, preserving meaningful multi-column width.
    while sum(widths) > budget:
        candidates = [j for j in range(n) if widths[j] > (4 if budget < n * 8 else 6)]
        if not candidates:
            break
        j = max(candidates, key=lambda x: (widths[x], x))
        widths[j] -= 1
    # Constrain extremely narrow environments to smallest viable table.
    sep = '─'
    def rule(left, middle, right):
        return indent + left + middle.join(sep * (w + 2) for w in widths) + right

    def stylize(text, tone):
        if color and tone in PALETTE:
            return PALETTE[tone] + text + RESET
        return text

    def render_cells(cells, header=False):
        wrapped = [_chunks(safe_text(c.text), widths[i]) for i, c in enumerate(cells)]
        height = max(map(len, wrapped))
        results = []
        for k in range(height):
            parts = []
            for i, pieces in enumerate(wrapped):
                string = pieces[k] if k < len(pieces) else ''
                padded = string + (' ' * max(0, widths[i] - visible_width(string)))
                parts.append(' ' + stylize(padded, 'info' if header else cells[i].tone) + ' ')
            results.append(indent + '│' + '│'.join(parts) + '│')
        return results

    lines = [rule('╭', '┬', '╮')]
    lines.extend(render_cells([Cell(h) for h in heads], header=True))
    lines.append(rule('├', '┼', '┤'))
    for index, row in enumerate(normalized):
        lines.extend(render_cells(row))
        if index < len(normalized) - 1:
            lines.append(rule('├', '┼', '┤'))
    lines.append(rule('╰', '┴', '╯'))
    return '\n'.join(lines)


def table(headers, rows, **kwargs):
    print(render_table(headers, rows, **kwargs), flush=True)
