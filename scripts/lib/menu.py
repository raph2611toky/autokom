#!/usr/bin/env python3
"""Keyboard menu AUTOKOM: arrows + Enter / numeric 1-9, /dev/tty even via curl|bash.

Yellow hints are only shown *while* the question is active and are removed
from the display after selection. A noninteractive shell must use CLI options.
"""
import os
import select
import shutil
import sys
import termios
import tty

TTY = '/dev/tty'


def _tty():
    try:
        return os.open(TTY, os.O_RDWR | os.O_NOCTTY)
    except OSError as exc:
        raise RuntimeError('Terminal interactif inaccessible : utiliser --non-interactive') from exc


def _send(fd: int, message: str) -> None:
    data = message.encode('utf-8')
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise RuntimeError('Écriture TTY impossible')
        data = data[count:]


def _hint(msg):
    if not msg:
        return ''
    width = max(20, shutil.get_terminal_size((100, 24)).columns - 8)
    # Avoid terminal line wraps (which would break cursor position calculations).
    msg = msg.replace('\n', ' ').strip()
    return f'\033[33m  ℹ {msg[:width]}\033[0m'


def pick(title: str, choices: list[str], default: int = 1, *, hint: str = '') -> int:
    if not choices or len(choices) > 9:
        raise ValueError('AUTOKOM : 1 à 9 choix pris en charge')
    if not 1 <= default <= len(choices):
        raise ValueError('Option par défaut hors limites')
    fd = _tty()
    initial = termios.tcgetattr(fd)
    choice = default - 1
    footer = '  ↑/↓ · Entrée · 1–' + str(len(choices)) + ' · q pour annuler'
    try:
        _send(fd, '\033[?25l')
        tty.setraw(fd)
        _send(fd, f'\r\n\033[1;36m{title}\033[0m\r\n')
        while True:
            for i, label in enumerate(choices):
                marker = '\033[32m❯\033[0m' if i == choice else ' '
                _send(fd, f'\r\033[2K  {marker} {i+1}. {label}\r\n')
            # Hint is inline with footer and erased on confirmation.
            instructions = _hint(hint) if hint else '\033[2m' + footer + '\033[0m'
            _send(fd, '\r\033[2K' + instructions)
            key = os.read(fd, 1)
            if not key:
                raise RuntimeError('Terminal fermé')
            if key in (b'\r', b'\n'):
                break
            if key in (b'\x03', b'q', b'Q'):
                raise KeyboardInterrupt
            if key == b'\x1b':
                ready, _, _ = select.select([fd], [], [], 0.2)
                if ready and os.read(fd, 1) == b'[':
                    ready, _, _ = select.select([fd], [], [], 0.2)
                    if ready:
                        arrow = os.read(fd, 1)
                        if arrow == b'A':
                            choice = (choice - 1) % len(choices)
                        elif arrow == b'B':
                            choice = (choice + 1) % len(choices)
            elif key in (b'1', b'2', b'3', b'4', b'5', b'6', b'7', b'8', b'9'):
                index = int(key) - 1
                if index < len(choices):
                    choice = index
                    break
            _send(fd, f'\033[{len(choices)}A')
        # Cursor is currently on footer: clear footer and all option lines,
        # then leave ONLY the confirmed result. No yellow hint remains.
        _send(fd, '\r\033[2K')
        _send(fd, f'\033[{len(choices)}A')
        for _ in choices:
            _send(fd, '\r\033[2K\r\n')
        _send(fd, f'\r\033[1;32m  ✓ {choices[choice]}\033[0m\r\n')
        return choice + 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, initial)
        try:
            _send(fd, '\033[?25h')
        finally:
            os.close(fd)


def ask(prompt: str, default: str = '', max_bytes: int = 4096, *, hint: str = '') -> str:
    fd = _tty()
    try:
        # A hint is printed ABOVE prompt, then erased after entering a value.
        has_hint = bool(hint)
        if has_hint:
            _send(fd, _hint(hint) + '\n')
        _send(fd, f'  {prompt}' + (f' [{default}]' if default else '') + ': ')
        data = bytearray()
        while True:
            key = os.read(fd, 1)
            if not key:
                raise RuntimeError('Terminal fermé pendant la saisie')
            if key in (b'\r', b'\n'):
                break
            data.extend(key)
            if len(data) > max_bytes:
                raise ValueError('Réponse trop longue')
        value = data.decode('utf-8').strip() or default
        if has_hint:
            # Input echoed and newline advanced one line; clear hint two up.
            # Do not remove user-entered prompt/answer.
            _send(fd, '\033[2A\r\033[2K\033[2B\r')
        return value
    finally:
        os.close(fd)


if __name__ == '__main__':
    print(pick(sys.argv[1], sys.argv[2:]))
