#!/usr/bin/env python3
import argparse
import importlib.util
import io
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import services
import menu


class CoreValidationTests(unittest.TestCase):
    def options(self, **overrides):
        vals = dict(db='postgresql', bind='local', allow='', unsafe_all=False,
                    tls='none', domain='jenkins.example.org', cert=None, key=None, email=None)
        vals.update(overrides)
        return argparse.Namespace(**vals)

    def test_domain_validation(self):
        self.assertEqual(services.valid_domain('Jenkins.Example.com'), 'jenkins.example.com')
        for domain in ('a', 'foo/', 'localhost', '-x.example.com', 'a b.example.com'):
            with self.assertRaises(ValueError):
                services.valid_domain(domain)

    def test_cidr_normalization(self):
        self.assertEqual(services.cidrs('192.0.2.5,10.1.2.7/24'), ['192.0.2.5/32','10.1.2.0/24'])
        with self.assertRaises(ValueError):
            services.cidrs('not an ip')

    def test_default_safety(self):
        self.assertEqual(services.check_user_input(self.options()).bind, 'local')
        with self.assertRaises(ValueError):
            services.check_user_input(self.options(bind='all'))
        with self.assertRaises(ValueError):
            services.check_user_input(self.options(bind='private'))
        with self.assertRaises(ValueError):
            services.check_user_input(self.options(db='oracle',bind='all',unsafe_all=True))

    def test_atomic_write_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d)/'data.txt'
            self.assertTrue(services.atomic_write(file, 'ABC'))
            self.assertFalse(services.atomic_write(file, 'ABC'))
            self.assertTrue(services.atomic_write(file, 'XYZ'))
            self.assertEqual(file.read_text(), 'XYZ')

    def test_menu_tty_numeric_and_arrow(self):
        for send, expected in [(b'3', 3), (b'\x1b[B\r', 2), (b'\r', 1)]:
            child, fd = pty.fork()
            if child == 0:
                try:
                    print('PICK_RESULT=', menu.pick('DATABASE?', ['PostgreSQL','MySQL','Oracle']), flush=True)
                    os._exit(0)
                except BaseException as e:
                    print('PICK_ERROR', repr(e), flush=True)
                    os._exit(2)
            try:
                time.sleep(0.15)
                os.write(fd, send)
                result = bytearray()
                deadline = time.monotonic()+5
                while time.monotonic()<deadline:
                    r,_,_=select.select([fd],[],[],0.1)
                    if r:
                        try: result.extend(os.read(fd,4096))
                        except OSError: break
                    if f'PICK_RESULT= {expected}'.encode() in result:
                        break
                _,status=os.waitpid(child,0)
                self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0, result.decode(errors='ignore'))
                self.assertIn(f'PICK_RESULT= {expected}'.encode(), result)
            finally:
                os.close(fd)

if __name__ == '__main__':
    unittest.main(verbosity=2)
