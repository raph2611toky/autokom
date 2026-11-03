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


def pick(title: str, choices: list[str], default: int = 1, *, hint: str = '',
         cancel_label: str | None = None) -> int:
    """Use the same AUTOKOM menu everywhere (arrows, Enter, digits).

    When cancel_label is set, an optional last line is reachable using arrows,
    the 0 key, or q/Ctrl-C. The return value 0 means an explicit cancellation;
    legacy callers and their 1-based selection numbers remain unchanged.
    """
    if not choices or len(choices) > (8 if cancel_label is not None else 9):
        raise ValueError('AUTOKOM : 1 à 9 choix pris en charge')
    if not 1 <= default <= len(choices):
        raise ValueError('Option par défaut hors limites')
    labels = list(choices) + ([cancel_label] if cancel_label is not None else [])
    fd = _tty()
    initial = termios.tcgetattr(fd)
    choice = default - 1
    footer = ('  ↑/↓ · Entrée · 1–' + str(len(choices)) +
              (' · 0/q pour annuler' if cancel_label is not None else ' · q pour annuler'))
    try:
        _send(fd, '\033[?25l')
        tty.setraw(fd)
        _send(fd, f'\r\n\033[1;36m{title}\033[0m\r\n')
        while True:
            for i, label in enumerate(labels):
                marker = '\033[32m❯\033[0m' if i == choice else ' '
                number = str(i + 1) if i < len(choices) else '0'
                _send(fd, f'\r\033[2K  {marker} {number}. {label}\r\n')
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
                            choice = (choice - 1) % len(labels)
                        elif arrow == b'B':
                            choice = (choice + 1) % len(labels)
            elif key == b'0' and cancel_label is not None:
                choice = len(choices)
                break
            elif key in (b'1', b'2', b'3', b'4', b'5', b'6', b'7', b'8', b'9'):
                index = int(key) - 1
                if index < len(choices):
                    choice = index
                    break
            _send(fd, f'\033[{len(labels)}A')
        # Keep only the final selection, no stale hint or duplicate menu.
        _send(fd, '\r\033[2K')
        _send(fd, f'\033[{len(labels)}A')
        for _ in labels:
            _send(fd, '\r\033[2K\r\n')
        if cancel_label is not None and choice == len(choices):
            _send(fd, f'\r\033[2m  - {cancel_label}\033[0m\r\n')
            return 0
        _send(fd, f'\r\033[1;32m  ✓ {labels[choice]}\033[0m\r\n')
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


def pick_paginated(title: str, choices: list[str], *, page_size: int = 9,
                   hint: str = '') -> int:
    """Global 1-based selection with 1..9 per page, ←/→ pagination and 0 cancel.

    Does not change existing pick() behavior for historical menus. Uses /dev/tty.
    """
    if not choices:return 0
    if not 1 <= page_size <= 9:raise ValueError('Page : entre 1 et 9 choix')
    fd=_tty();initial=termios.tcgetattr(fd)
    page=0;cursor=0;pages=(len(choices)+page_size-1)//page_size
    slots=page_size+1
    def send(s):_send(fd,s)
    def draw():
        nonlocal cursor
        start=page*page_size
        visible=choices[start:start+page_size]
        cursor=min(cursor,len(visible))
        width=max(50,shutil.get_terminal_size((100,24)).columns-12)
        for i in range(page_size):
            if i<len(visible):
                marker='\033[32m❯\033[0m' if cursor==i else ' '
                label=visible[i].replace('\n',' ')[:max(10,width-10)]
                line=f'  {marker} {i+1}. {label}'
            else:line=''
            send('\r\033[2K'+line+'\r\n')
        marker='\033[32m❯\033[0m' if cursor==len(visible) else ' '
        send(f'\r\033[2K  {marker} 0. Annuler\r\n')
        label=hint or '↑/↓ choix · ←/→ pages · 1-9 sélectionner · 0/q annuler'
        send('\r\033[2K\033[33m  ℹ '+label[:width]+f' · Page {page+1}/{pages}\033[0m')
    def up():send(f'\033[{slots}A')
    try:
        tty.setraw(fd);send('\033[?25l')
        send('\r\n\033[1;36m'+title+'\033[0m\r\n')
        while True:
            draw()
            key=os.read(fd,1)
            if not key:raise RuntimeError('Terminal fermé')
            if key in (b'q',b'Q',b'\x03',b'0'):
                result=0;break
            if key in (b'\r',b'\n'):
                result=0 if cursor==len(choices[page*page_size:page*page_size+page_size]) else page*page_size+cursor+1
                break
            previous=page
            visible=choices[page*page_size:(page+1)*page_size]
            if key==b'\x1b':
                ready,_,_=select.select([fd],[],[],0.15)
                if ready and os.read(fd,1)==b'[':
                    ready,_,_=select.select([fd],[],[],0.15)
                    if ready:
                        arrow=os.read(fd,1)
                        if arrow==b'A':cursor=(cursor-1)%(len(visible)+1)
                        elif arrow==b'B':cursor=(cursor+1)%(len(visible)+1)
                        elif arrow==b'C':page=min(page+1,pages-1);cursor=0
                        elif arrow==b'D':page=max(0,page-1);cursor=0
            elif key in (b'>',b'n',b'N',b'l',b'L'):
                page=min(page+1,pages-1);cursor=0
            elif key in (b'<',b'p',b'P',b'h',b'H'):
                page=max(0,page-1);cursor=0
            elif b'1'<=key<=b'9':
                i=int(key)-1
                if i<len(visible):result=page*page_size+i+1;break
            up()
        # Erase the same number of rows every time, including the last page.
        send('\r\033[2K');up()
        for _ in range(slots):send('\r\033[2K\r\n')
        if result:
            send('\r\033[1;32m  ✓ '+choices[result-1].replace('\n',' ')[:72]+'\033[0m\r\n')
        else:send('\r\033[2m  - Annulé\033[0m\r\n')
        return result
    finally:
        termios.tcsetattr(fd,termios.TCSADRAIN,initial)
        try:send('\033[?25h')
        finally:os.close(fd)
