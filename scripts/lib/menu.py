#!/usr/bin/env python3
"""AUTOKOM terminal menus: /dev/tty, not stdin (curl | bash safe).

All input/output uses os.read/os.write on the same unbuffered fd.
Never open /dev/tty as 'r+' text: switching buffered read/write may seek on a TTY.
"""
import os
import select
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
        written = os.write(fd, data)
        if written <= 0:
            raise RuntimeError('Écriture terminal impossible')
        data = data[written:]


def pick(title: str, choices: list[str], default: int = 1) -> int:
    if not choices or len(choices) > 9:
        raise ValueError('AUTOKOM : 1 à 9 options prises en charge')
    if not 1 <= default <= len(choices):
        raise ValueError('Option par défaut hors limites')
    fd = _tty()
    original = termios.tcgetattr(fd)
    choice = default - 1
    try:
        _send(fd, '\033[?25l')
        tty.setraw(fd)
        _send(fd, f'\r\n\033[1;36m{title}\033[0m\r\n')
        while True:
            for i, option in enumerate(choices):
                cursor = '\033[1;32m❯\033[0m' if i == choice else ' '
                _send(fd, f'\033[2K\r  {cursor}  {i+1}. {option}\r\n')
            _send(fd, f'\033[2K\r  \033[2m↑/↓ · Entrée · 1–{len(choices)} · q pour annuler\033[0m')
            key = os.read(fd, 1)
            if not key:
                raise RuntimeError('Terminal fermé')
            if key in (b'\r', b'\n'):
                _send(fd, f'\r\033[2K\033[1;32m  ✓ {choices[choice]}\033[0m\r\n')
                return choice + 1
            if key in (b'\x03', b'q', b'Q'):
                raise KeyboardInterrupt
            if key == b'\x1b':
                # Arrow keys ESC [ A/B, avoid blocking forever on isolated ESC.
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
                    _send(fd, f'\r\033[2K\033[1;32m  ✓ {choices[choice]}\033[0m\r\n')
                    return choice + 1
            _send(fd, f'\033[{len(choices)}A')
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        try:
            _send(fd, '\033[?25h')
        finally:
            os.close(fd)


def ask(prompt: str, default: str = '', max_bytes: int = 4096) -> str:
    """Read one line via /dev/tty without buffered r+ or seeking."""
    fd = _tty()
    try:
        _send(fd, f'  {prompt}' + (f' [{default}]' if default else '') + ': ')
        buffer = bytearray()
        while True:
            chunk = os.read(fd, 1)
            if not chunk:
                raise RuntimeError('Terminal fermé pendant la saisie')
            if chunk in (b'\n', b'\r'):
                break
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise ValueError('Réponse trop longue')
        return buffer.decode('utf-8').strip() or default
    finally:
        os.close(fd)


if __name__ == '__main__':
    print(pick(sys.argv[1], sys.argv[2:]))
