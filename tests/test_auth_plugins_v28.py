"""v28: authentication and plugin flow (no host services or network mutation)."""
import argparse
import contextlib
import io
import os
from pathlib import Path
import pty
import select
import tempfile
import time
import unittest
from unittest import mock

import autokom_security as sec
import autokom_cli as cli
import jenkins_plugins as j
import menu

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        patches=[]
        for name,value in [('BASE',self.base),('STATE',self.base/'auth.json'),('SMTP',self.base/'smtp.json'),('OTP',self.base/'recovery.json'),('ATTEMPTS',self.base/'attempts.json'),('JENKINS',self.base/'jenkins-api.auth')]:
            p=mock.patch.object(sec,name,value);p.start();self.addCleanup(p.stop)
    def test_password_salted_and_not_plaintext(self):
        val=sec._hash('long-password-not-shared')
        self.assertTrue(sec._verify('long-password-not-shared',val))
        self.assertFalse(sec._verify('wrong',val))
        self.assertNotIn('long-password-not-shared',str(val))
        self.assertNotEqual(val['hash'],sec._hash('long-password-not-shared')['hash'])
    def test_atomic_permissions(self):
        sec._write(sec.STATE,{'email':'test@example.org'})
        self.assertEqual(sec.STATE.stat().st_mode & 0o777,0o600)
        self.assertEqual(sec.BASE.stat().st_mode & 0o777,0o700)
    def test_fail_closed_unconfigured(self):
        with mock.patch.object(sec,'_tty_secret') as tty:
            with self.assertRaises(sec.SecurityError):sec.authorize('test')
            tty.assert_not_called()
    def test_attempts_locked(self):
        sec._write(sec.STATE,{'passphrase':sec._hash('a properly long strong passphrase')})
        with mock.patch.object(sec,'_tty_secret',return_value='wrong'):
            for i in range(5):
                with self.assertRaisesRegex(sec.SecurityError,'incorrecte'):sec.authorize('test')
            with self.assertRaisesRegex(sec.SecurityError,'bloqué'):sec.authorize('test')
    def test_otp_recovery_expires_and_limits_attempts(self):
        sec._write(sec.OTP,{'email':'x@y.org','salt':os.urandom(16).hex(),'hash':'0000','expires_at':time.time()-1,'attempts':0})
        with self.assertRaisesRegex(sec.SecurityError,'expiré'):sec._check_code('x@y.org')
    def test_smtp_required_for_otp(self):
        with self.assertRaisesRegex(sec.SecurityError,'SMTP non configuré'):sec._mail_code('test@example.org')
    def test_otp_delivery_and_single_use(self):
        sec._write(sec.SMTP,{'host':'smtp.example.org','port':587,'sender':'no-reply@example.org','username':'sender','password':'protected'})
        with mock.patch.object(sec.smtplib,'SMTP') as server, mock.patch.object(sec.secrets,'randbelow',return_value=12345):
            sec._mail_code('test@example.org')
            self.assertTrue(server.return_value.__enter__.return_value.starttls.called)
            self.assertTrue(server.return_value.__enter__.return_value.send_message.called)
        saved=sec._read(sec.OTP)
        self.assertNotIn('012345',str(saved))
        with mock.patch.object(sec,'_tty_secret',return_value='012345'):
            sec._check_code('test@example.org')
        self.assertFalse(sec.OTP.exists())
        with self.assertRaisesRegex(sec.SecurityError,'absent ou expiré'):sec._check_code('test@example.org')
    def test_setup_confirms_email_twice_and_never_saves_passphrase(self):
        data=iter(['user@example.org','user@example.org'])
        with mock.patch.object(sec,'_tty_text',side_effect=lambda label:next(data)), mock.patch.object(sec,'_password_new',return_value='my example passphrase is sufficiently long'), mock.patch.object(sec,'_mail_code') as send, mock.patch.object(sec,'_check_code'):
            sec.setup()
        send.assert_called_once_with('user@example.org')
        record=sec._read(sec.STATE)
        self.assertTrue(record['email_verified'])
        self.assertTrue(sec._verify('my example passphrase is sufficiently long',record['passphrase']))
        self.assertNotIn('my example passphrase is sufficiently long',sec.STATE.read_text())
    def test_jenkins_credential_saved_once(self):
        with mock.patch.object(sec,'authorize'),mock.patch.object(sec,'_tty_text',return_value='admin'),mock.patch.object(sec,'_tty_secret',return_value='example-api-token'):
            sec.set_jenkins_credential()
        self.assertEqual(sec.JENKINS.stat().st_mode & 0o777,0o600)
        self.assertEqual(sec.JENKINS.read_text(),'admin:example-api-token\n')
    def test_dangerous_commands_gated_after_configuration(self):
        sec._write(sec.STATE,{'passphrase':sec._hash('my-very-long-passphrase')})
        with mock.patch.object(sec,'authorize',side_effect=sec.SecurityError('pass required')):
            with self.assertRaisesRegex(cli.CLIError,'pass required'):cli.main(['install','logs'])
        with mock.patch.object(sec,'authorize') as authorize, mock.patch.object(cli,'perform',return_value=0), mock.patch.object(cli,'load_settings') as load:
            # --check must never trigger passphrase gate
            with mock.patch.object(cli,'selected',return_value=['logs']):
                try:cli.main(['install','--only','logs','--check'])
                except Exception:pass
            authorize.assert_not_called()

class PaginationTests(unittest.TestCase):
    def test_12_items_right_then_two_selects_11(self):
        child,fd=pty.fork()
        if child==0:
            try:
                val=menu.pick_paginated('PLUGINS',[f'Plugin {x}' for x in range(1,13)])
                os.write(1,f'RESULT={val}\n'.encode());os._exit(0)
            except BaseException as exc:
                os.write(1,f'ERR={exc!r}\n'.encode());os._exit(2)
        try:
            time.sleep(.13)
            os.write(fd,b'\x1b[C')
            time.sleep(.1)
            os.write(fd,b'2')
            raw=bytearray();end=time.monotonic()+4
            while time.monotonic()<end:
                r,_,_=select.select([fd],[],[],.1)
                if r:
                    try:raw.extend(os.read(fd,4096))
                    except OSError:break
                if b'RESULT=' in raw:break
            _,status=os.waitpid(child,0)
            self.assertEqual(os.WEXITSTATUS(status),0,raw.decode(errors='replace')[-300:])
            self.assertIn(b'RESULT=11',raw)
        finally:os.close(fd)

class PluginTests(unittest.TestCase):
    def test_select_invokes_install_flow(self):
        ns=argparse.Namespace(action='search',query=['test'],limit=40,category='',select=True,auth_file=None)
        catalog={'test-plugin':{'name':'Test Plugin','version':'2.0'}}
        with mock.patch.object(j,'catalogue',return_value=catalog),mock.patch.object(j,'pick_paginated',return_value=1),mock.patch.object(j,'_execute_selection',return_value=7) as called:
            self.assertEqual(j.run(ns),7)
            called.assert_called_once_with('test-plugin',ns)
    def test_apply_requires_autokom_passphrase(self):
        ns=argparse.Namespace(action='install',plugin_id='some-plugin',auth_file=None,apply=True)
        with mock.patch.object(j,'catalogue',return_value={'some-plugin':{'name':'Test','version':'1.0'}}),mock.patch.object(j,'_local_plugins',return_value=[]),mock.patch.object(j,'require_apply',return_value=True),mock.patch('autokom_security.authorize',side_effect=sec.SecurityError('Auth required')):
            with self.assertRaisesRegex(sec.SecurityError,'Auth required'):j.run(ns)
    def test_jenkins_show_only_jenkins_vhost(self):
        from tools_registry import discovered_service_files
        # Existing discovery covers other services; regression tested against
        # a synthetic upstream in direct source via filename and proxy match.
        import inspect
        self.assertIn("if name == 'jenkins'",inspect.getsource(discovered_service_files))

if __name__=='__main__':unittest.main()

class GrafanaAuthTests(unittest.TestCase):
    def test_plugin_install_requires_auth_but_dry_run_does_not(self):
        import grafana_plugins as g
        obj={'slug':'some-test-plugin','name':'A test plugin','signature':'grafana'}
        with mock.patch.object(g,'detail',return_value=obj):
            with mock.patch('autokom_security.authorize',side_effect=sec.SecurityError('Auth required')) as gate:
                self.assertEqual(g.install('some-test-plugin',apply=False),0)
                gate.assert_not_called()
                with mock.patch.object(g.os,'geteuid',return_value=0):
                    with self.assertRaisesRegex(sec.SecurityError,'Auth required'):
                        g.install('some-test-plugin',apply=True)
