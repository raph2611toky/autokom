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
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import services
import menu


class CoreValidationTests(unittest.TestCase):
    def options(self, **overrides):
        vals = dict(db='postgresql', bind='local', allow='', unsafe_all=False, allow_public_clients=False, confirm_restart=False, non_interactive=False, create_app_db=False,
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
        self.assertEqual(services.check_user_input(self.options(bind='all')).bind, 'all')
        self.assertEqual(services.check_user_input(self.options(bind='private')).bind, 'all')
        with self.assertRaises(ValueError):
            services.check_user_input(self.options(db='oracle',bind='all',unsafe_all=True))

    def test_bind_all_does_not_authorize_every_client(self):
        opts = self.options(db='mysql', bind='all', unsafe_all=True, allow='')
        opts = services.check_user_input(opts)
        self.assertEqual(services.mysql_requested_address(opts), '0.0.0.0')
        self.assertEqual(services.mysql_hosts_for_options(opts), ['localhost'])
        opts = self.options(db='mysql', bind='all', unsafe_all=True, allow='172.18.0.0/16')
        opts = services.check_user_input(opts)
        self.assertIn('172.18.0.0/255.255.0.0', services.mysql_hosts_for_options(opts))
        self.assertNotIn('%', services.mysql_hosts_for_options(opts))
        with self.assertRaises(ValueError):
            services.check_user_input(self.options(db='mysql', bind='all', unsafe_all=True, allow='0.0.0.0/0'))

    def test_mysql_bind_comparison(self):
        self.assertTrue(services.mysql_bind_equivalent('0.0.0.0', '0.0.0.0'))
        self.assertFalse(services.mysql_bind_equivalent('127.0.0.1', '0.0.0.0'))
        self.assertFalse(services.mysql_bind_equivalent('*', '0.0.0.0'))  # IPv6 exposure differs

    def test_existing_mysql_wildcard_no_restart_or_config_write(self):
        opts = self.options(db='mysql', bind='all', unsafe_all=True)
        opts = services.check_user_input(opts)
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            query = cmd[-1] if cmd else ''
            if cmd == ['ss', '-ltn']:
                return SimpleNamespace(returncode=0, stdout='State Recv-Q Send-Q Local Address:Port Peer Address:Port\nLISTEN 0 100 0.0.0.0:3306 0.0.0.0:*\n')
            if query == 'SELECT @@GLOBAL.bind_address':
                return SimpleNamespace(returncode=0, stdout='0.0.0.0\n')
            if 'COUNT(*)' in query:
                return SimpleNamespace(returncode=0, stdout='0\n')
            return SimpleNamespace(returncode=0, stdout='1\n')
        with mock.patch.object(services, 'run', fake_run), \
             mock.patch.object(services, 'write_secret', return_value='test-password'), \
             mock.patch.object(services, 'backup', side_effect=AssertionError('Unexpected backup')), \
             mock.patch.object(services, 'atomic_write', side_effect=AssertionError('Unexpected config write')), \
             mock.patch.object(services, 'say'):
            services.mysql_config(opts)
        self.assertFalse(any(cmd[:3] == ['systemctl', 'restart', 'mysql'] for cmd in calls))
        self.assertTrue(any(cmd[0] == 'mysql' for cmd in calls))
        self.assertFalse(any('CREATE DATABASE' in str(cmd) or 'GRANT ALL' in str(cmd) for cmd in calls))

    def test_tty_question_after_numeric_menu(self):
        child, fd = pty.fork()
        if child == 0:
            try:
                selected = menu.pick('CHOIX', ['PostgreSQL', 'MySQL', 'Oracle'])
                value = menu.ask('CIDR clients', '')
                print(f'ANSWER={selected};{value}', flush=True)
                os._exit(0)
            except BaseException as error:
                print(f'ERROR={error!r}', flush=True)
                os._exit(2)
        try:
            time.sleep(0.15)
            os.write(fd, b'2')
            time.sleep(0.15)
            os.write(fd, b'172.18.0.0/16\n')
            output = bytearray()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                readable, _, _ = select.select([fd], [], [], 0.1)
                if readable:
                    try: output.extend(os.read(fd, 4096))
                    except OSError: break
                if b'ANSWER=2;172.18.0.0/16' in output:
                    break
            _, status = os.waitpid(child, 0)
            self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, output.decode(errors='ignore'))
            self.assertIn(b'ANSWER=2;172.18.0.0/16', output)
        finally:
            os.close(fd)

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
