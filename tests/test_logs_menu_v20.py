"""AUTOKOM logs menu: real TTY arrow/digit paths and cancel safety."""
import contextlib
import io
import os
from pathlib import Path
import pty
import select
import sys
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_cli as cli
import logging_stack
import menu

GIB=1024**3


def tty_session(keys: bytes) -> tuple[int, str]:
    """Fork with controlling TTY; children never access real infra."""
    pid, fd = pty.fork()
    if pid == 0:
        # pytest capture redirects sys.stdout; bind the child output to its PTY.
        sys.stdout = os.fdopen(os.dup(1), "w", buffering=1)
        try:
            with mock.patch.object(logging_stack,'platform_resources',return_value=(int(7.6*GIB),50*GIB)):
                result=cli.choose_logs_backend('both')
            print(f'OUTCOME={result}',flush=True)
            os._exit(0)
        except KeyboardInterrupt:
            print('OUTCOME=CANCEL',flush=True)
            os._exit(0)
        except BaseException as exc:
            print(f'OUTCOME=ERROR {exc!r}',flush=True)
            os._exit(5)
    data=bytearray()
    try:
        deadline=time.monotonic()+5
        sent=False
        while time.monotonic()<deadline:
            ready,_,_=select.select([fd],[],[],0.1)
            if ready:
                try:
                    part=os.read(fd,8192)
                except OSError:
                    break
                if not part:
                    break
                data.extend(part)
            if not sent and b'1. Loki' in data and b'0. Annuler' in data:
                os.write(fd,keys)
                sent=True
            if b'OUTCOME=' in data:
                break
        if not sent:
            raise AssertionError('Menu not displayed: '+data.decode(errors='replace'))
    finally:
        # Attendre la terminaison AVANT de fermer le PTY : la fermeture du
        # terminal maître peut envoyer SIGHUP au processus fils, même après
        # l'écriture de OUTCOME (course observée sous charge CI).
        if b'OUTCOME=' in data:
            # The child emits OUTCOME immediately before os._exit(0). Waiting
            # for that exit prevents a false failure caused by WNOHANG racing
            # with the child's last write.
            _, status = os.waitpid(pid,0)
        else:
            ended, status = os.waitpid(pid,os.WNOHANG)
            if not ended:
                os.kill(pid,9)
                _,status=os.waitpid(pid,0)
        # Le fils peut avoir écrit la dernière ligne sur le PTY pendant
        # waitpid ; drainer les octets restants avant de fermer le maître.
        for _ in range(20):
            ready,_,_=select.select([fd],[],[],0.02)
            if not ready: break
            try: part=os.read(fd,8192)
            except OSError: break
            if not part: break
            data.extend(part)
        os.close(fd)
    return status,data.decode(errors='replace')


class LogsKeyboardMenu(unittest.TestCase):
    def test_number_two_selects_opensearch_without_enter(self):
        status,text=tty_session(b'2')
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,text)
        self.assertIn('OUTCOME=opensearch',text)

    def test_arrow_down_and_enter_selects_opensearch(self):
        status,text=tty_session(b'\x1b[B\r')
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,text)
        self.assertIn('OUTCOME=opensearch',text)
        self.assertNotIn('^[[B',text) # not echoed as ^[[... in canonical mode

    def test_arrow_up_and_enter_cancels(self):
        status,text=tty_session(b'\x1b[A\r')
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,text)
        self.assertIn('OUTCOME=CANCEL',text)

    def test_number_zero_cancels_without_enter(self):
        status,text=tty_session(b'0')
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,text)
        self.assertIn('OUTCOME=CANCEL',text)

    def test_q_cancels(self):
        status,text=tty_session(b'q')
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,text)
        self.assertIn('OUTCOME=CANCEL',text)

    def test_enter_defaults_to_loki(self):
        status,text=tty_session(b'\r')
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,text)
        self.assertIn('OUTCOME=loki',text)


if __name__=='__main__':
    unittest.main()
