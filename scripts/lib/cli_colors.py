"""ANSI statuses used by AUTOKOM: readable, colorful, TTY-only and safe for logs."""
from __future__ import annotations
import os
import re
import sys

RESET = "\x1b[0m"
PALETTE = {
    "ok": "\x1b[38;2;116;238;174m",          # success green
    "error": "\x1b[38;2;255;110;130m",       # failure red
    "warn": "\x1b[38;2;255;201;100m",        # warning amber
    "action": "\x1b[38;2;86;224;241m",       # actions cyan
    "info": "\x1b[38;2;132;182;255m",        # details blue
    "heading": "\x1b[1;38;2;199;147;255m", # titles violet
    "muted": "\x1b[38;2;158;165;183m",      # skip gray
}
_MARKERS = {'✓':'ok','✗':'error','!':'warn','›':'info','+':'action','◈':'heading'}
# Only standalone status markers, never path components or positive arithmetic.
_INLINE_MARKER = re.compile(r'(?<![\w/])([✓✗◈›])(?=\s|$)|(?<![\w/])([!+])(?=\s)')
_STATUSES = re.compile(r'(?<![\w/.-])(OK|PASS|UP|DOWN|FAIL|FAILED|ABSENTE|ABSENT|ERREUR|ERROR|WARNING|SIMULATION|APPLICATION)(?![\w/.-])')
_ROLE = re.compile(r'^(\s*)(Backend Django|Journaux|Frontend|Domaine|Politique|Logs|BILAN|AUTOKOM / REPRISE AUTOMATIQUE|AUTOKOM / BILAN)(?=\s*:|\s*·|\s*$)')
_STATUS_COLORS = {'OK':'ok','PASS':'ok','UP':'ok','DOWN':'error','FAIL':'error',
                  'FAILED':'error','ABSENT':'error','ABSENTE':'error',
                  'ERREUR':'error','ERROR':'error','WARNING':'warn',
                  'SIMULATION':'heading','APPLICATION':'heading'}

def enabled(target):
    return (getattr(target, 'isatty', lambda: False)()
            and 'NO_COLOR' not in os.environ
            and os.environ.get('TERM', '') not in ('', 'dumb'))

def highlight(line):
    # Replace tokens in one pass to prevent recoloring existing ANSI sequences.
    def mark(m):
        item = m.group(1) or m.group(2)
        return PALETTE[_MARKERS[item]] + item + RESET
    line = _INLINE_MARKER.sub(mark, line)
    line = _STATUSES.sub(lambda m: PALETTE[_STATUS_COLORS[m.group(1)]] + m.group(1) + RESET, line)
    line = _ROLE.sub(lambda m: m.group(1) + PALETTE['heading'] + m.group(2) + RESET, line)
    return line

def pretty_print(*args, sep=' ', end='\n', file=None, flush=False):
    target = file if file is not None else sys.stdout
    body = sep.join(str(arg) for arg in args)
    if enabled(target):
        body = '\n'.join(highlight(line) for line in body.split('\n'))
    target.write(body + end)
    if flush:
        target.flush()
