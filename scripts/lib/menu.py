#!/usr/bin/env python3
"""Single-key terminal menu. Uses /dev/tty so it works with curl | bash."""
import os
import sys
import termios
import tty


def pick(title: str, choices: list[str], default: int = 1) -> int:
    if not choices or len(choices) > 9:
        raise ValueError('AUTOKOM supports 1 to 9 choices')
    if not os.path.exists('/dev/tty'):
        raise RuntimeError('Interactive terminal unavailable; use explicit --db/--bind options')
    fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    old = termios.tcgetattr(fd)
    choice = default - 1
    out = lambda value: os.write(fd, value.encode('utf-8'))
    try:
        out('\033[?25l')
        tty.setraw(fd)
        out(f'\r\n\033[1;36m{title}\033[0m\r\n')
        while True:
            for i, option in enumerate(choices):
                cursor = '\033[1;32m❯\033[0m' if i == choice else ' '
                out(f'\033[2K\r  {cursor}  {i+1}. {option}\r\n')
            out('\033[2K\r  \033[2m↑/↓ · Entrée · touche 1–{0} · q pour annuler\033[0m'.format(len(choices)))
            c = os.read(fd, 1)
            if c in (b'\r', b'\n'):
                out('\r\033[2K\033[1;32m  ✓ ' + choices[choice] + '\033[0m\r\n')
                return choice + 1
            if c == b'\x03' or c.lower() == b'q':
                raise KeyboardInterrupt
            if c == b'\x1b':
                seq = os.read(fd, 1) + os.read(fd, 1)
                if seq == b'[A':
                    choice = (choice - 1) % len(choices)
                elif seq == b'[B':
                    choice = (choice + 1) % len(choices)
            elif c.decode('ascii', errors='ignore') in '123456789':
                idx = int(c) - 1
                if idx < len(choices):
                    choice = idx
                    out('\r\033[2K\033[1;32m  ✓ ' + choices[choice] + '\033[0m\r\n')
                    return choice + 1
            out(f'\033[{len(choices)}A')
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out('\033[?25h')
        os.close(fd)


def ask(prompt: str, default: str = '') -> str:
    with open('/dev/tty', 'r+', encoding='utf-8', buffering=1) as terminal:
        terminal.write(f'  {prompt}' + (f' [{default}]' if default else '') + ': ')
        terminal.flush()
        value = terminal.readline().strip()
        return value or default


if __name__ == '__main__':
    print(pick(sys.argv[1], sys.argv[2:]))
