"""Regressions for actual Enter handling and Jenkins admin form retries."""
import fcntl
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import termios
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import jenkins_postinstall as ui
import jenkins_firstboot as firstboot


class JenkinsPasswordValidation(unittest.TestCase):
    def test_enter_not_treated_as_password_character(self):
        self.assertEqual(firstboot.validate_admin_password('MotDePasseSecurise!'), 'MotDePasseSecurise!')

    def test_validation_message_distinguishes_empty_short_long_and_newline(self):
        with self.assertRaisesRegex(ValueError, 'vide'):
            firstboot.validate_admin_password('')
        with self.assertRaisesRegex(ValueError, 'trop court'):
            firstboot.validate_admin_password('abc')
        with self.assertRaisesRegex(ValueError, 'trop long'):
            firstboot.validate_admin_password('a' * 257)
        with self.assertRaisesRegex(ValueError, 'caractères de contrôle'):
            firstboot.validate_admin_password('password-12345678\n')

    def test_secret_is_not_trimmed(self):
        with mock.patch.object(ui.os, 'open', return_value=3), \
             mock.patch.object(ui.os, 'close'), \
             mock.patch.object(ui.getpass, 'getpass', return_value='  MotDePasseLongEtSur  '):
            self.assertEqual(ui.read_initial_admin_password('Test'), '  MotDePasseLongEtSur  ')

    def test_identity_remains_after_short_secret(self):
        identity = ['AdminJenkins','Administrateur','admin@ikom.mg']
        valid = 'UnMotDePasseValide-2026'
        with mock.patch.object(ui,'selected_admin',return_value=None), \
             mock.patch.object(ui,'ask',side_effect=identity) as ask, \
             mock.patch.object(ui,'read_initial_admin_password',side_effect=['court',valid,valid]) as secret, \
             mock.patch.object(ui,'save_admin_choice',return_value={'username':'AdminJenkins'}) as save, \
             mock.patch.object(ui,'output') as output:
            self.assertEqual(ui.choose_initial_administrator()['username'],'AdminJenkins')
            self.assertEqual(ask.call_count,3) # identity asked only once
            self.assertEqual(secret.call_count,3) # invalid, valid, confirmation
            save.assert_called_once_with(*identity,valid)
            self.assertTrue(any('trop court' in str(call) for call in output.call_args_list))

    def test_mismatch_retries_password_only(self):
        identity = ['AdminJenkins','Administrateur','admin@ikom.mg']
        password='UnMotDePasseValide-2026'
        with mock.patch.object(ui,'selected_admin',return_value=None), \
             mock.patch.object(ui,'ask',side_effect=identity) as ask, \
             mock.patch.object(ui,'read_initial_admin_password',side_effect=[password,'wrong-password-12345',password,password]) as secret, \
             mock.patch.object(ui,'save_admin_choice',return_value={'username':'AdminJenkins'}):
            ui.choose_initial_administrator()
        self.assertEqual(ask.call_count,3)
        self.assertEqual(secret.call_count,4)

    def test_real_tty_masked_password_enter(self):
        # This test uses the same Unix /dev/tty + getpass path as sudo autokom install.
        if os.name != 'posix':
            self.skipTest('Pseudo-terminal POSIX requis')
        secret = 'P@sswordLong-Secure2026'
        code = ("from jenkins_postinstall import read_initial_admin_password; "
                "p=read_initial_admin_password('Mot de passe Jenkins'); "
                "print('AUTOKOM_PASSWORD_OK' if p == 'P@sswordLong-Secure2026' "
                "else 'AUTOKOM_PASSWORD_MISMATCH', flush=True)")
        master, slave = pty.openpty()
        def setup_controlling_tty():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        proc = None
        output=b''
        try:
            env={**os.environ,'PYTHONPATH':str(ROOT/'scripts/lib')}
            proc=subprocess.Popen([sys.executable,'-c',code],stdin=slave,stdout=slave,stderr=slave,
                                  preexec_fn=setup_controlling_tty,env=env)
            os.close(slave);slave=-1
            deadline=time.monotonic()+8
            sent=False
            while time.monotonic()<deadline and proc.poll() is None:
                ready,_,_=select.select([master],[],[],0.1)
                if ready:
                    try:
                        part=os.read(master,8192)
                    except OSError:
                        break
                    output+=part
                    if b'Mot de passe Jenkins:' in output and not sent:
                        os.write(master,(secret+'\r').encode())
                        sent=True
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
            try:
                output+=os.read(master,8192)
            except OSError:
                pass
            self.assertEqual(proc.returncode,0,output.decode(errors='replace'))
            self.assertIn(b'AUTOKOM_PASSWORD_OK',output)
            # Input was not echoed to the pseudo-terminal's output stream.
            self.assertNotIn(secret.encode(),output)
        finally:
            if slave>=0: os.close(slave)
            os.close(master)
            if proc is not None and proc.poll() is None:
                proc.kill();proc.wait()


if __name__=='__main__':
    unittest.main()
