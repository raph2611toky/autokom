"""v29 regression: real TTY prompts, SMTP form, TLS, legacy schema and privacy."""
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


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        for name, value in [('BASE',base),('STATE',base/'auth.json'),
                            ('SMTP',base/'smtp.json'),('OTP',base/'recovery.json'),
                            ('ATTEMPTS',base/'attempts.json'),('JENKINS',base/'jenkins-api.auth'),
                            ('RECOVERY_CODES',base/'recovery-codes.json'),('VAULT_KEY',base/'vault.key')]:
            patch = mock.patch.object(sec, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_smtp_config_explicit_keys_and_permissions(self):
        inputs = iter(['smtp.example.org','587','admin@example.org',
                       'no-reply@example.org','operator@example.org','operator@example.org'])
        with (mock.patch.object(sec,'_tty_text',side_effect=lambda _:next(inputs)),
              mock.patch.object(sec,'_tty_secret',return_value='not-visible'),
              mock.patch.object(sec,'_smtp_connection') as conn):
            sec.smtp_setup()
            conn.assert_called_once()
            self.assertTrue(conn.return_value.__enter__.return_value.starttls.called)
        saved = sec._read(sec.SMTP)
        self.assertEqual(set(saved),{'EMAIL_HOST','EMAIL_PORT','EMAIL_LOGIN','EMAIL_MDP_ENCRYPTED','EMAIL_FROM','EMAIL_ADMIN'})
        self.assertEqual(saved['EMAIL_ADMIN'],'operator@example.org')
        self.assertNotIn('not-visible',str(saved))
        self.assertEqual(sec._smtp_values(saved)['password'],'not-visible')
        self.assertEqual(sec.SMTP.stat().st_mode & 0o777,0o600)
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):
            sec.main(['status'])
        self.assertNotIn('not-visible',buf.getvalue())
        self.assertNotIn('operator@example.org',buf.getvalue())

    def test_smtp_email_admin_confirmation_precedes_any_connection(self):
        inputs=iter(['smtp.example.org','587','jane','no-reply@example.org','a@example.org','wrong@example.org'])
        with (mock.patch.object(sec,'_tty_text',side_effect=lambda _:next(inputs)),
              mock.patch.object(sec,'_tty_secret',return_value='secret'),
              mock.patch.object(sec,'_smtp_connection') as conn):
            with self.assertRaisesRegex(sec.SecurityError,'invalide'):
                sec.smtp_setup()
            conn.assert_not_called()
        self.assertFalse(sec.SMTP.exists())

    def test_implicit_tls_port465(self):
        c={'host':'smtp.example.org','port':465,'login':'admin','password':'test'}
        with mock.patch.object(sec.smtplib,'SMTP_SSL') as ssl_srv, mock.patch.object(sec.smtplib,'SMTP') as plain:
            with sec._smtp_connection(c) as server:
                sec._smtp_secure(server,c)
        ssl_srv.assert_called_once()
        plain.assert_not_called()
        ssl_srv.return_value.__enter__.return_value.starttls.assert_not_called()

    def test_auth_setup_without_smtp_fails_before_terminal_prompt(self):
        with mock.patch.object(sec,'_tty_text') as question:
            with self.assertRaisesRegex(sec.SecurityError,'SMTP non configuré'):
                sec.setup()
            question.assert_not_called()

    def test_legacy_v28_smtp_otp_still_supported(self):
        sec._write(sec.SMTP,{'host':'smtp.example.org','port':587,'sender':'robot@example.org',
                             'username':'robot','password':'old-secret'})
        with mock.patch.object(sec.smtplib,'SMTP') as srv:
            sec._mail_code('admin@example.org')
            self.assertTrue(srv.return_value.__enter__.return_value.starttls.called)
            self.assertTrue(srv.return_value.__enter__.return_value.send_message.called)

    def test_setup_uses_configured_email_admin_without_duplicate_prompt(self):
        sec._write(sec.SMTP,{'EMAIL_HOST':'smtp.example.org','EMAIL_PORT':587,
                              'EMAIL_LOGIN':'','EMAIL_MDP':'','EMAIL_FROM':'a@example.org',
                              'EMAIL_ADMIN':'valid@example.org'})
        with mock.patch.object(sec,'_tty_text',side_effect=AssertionError('email prompted twice')),\
             mock.patch.object(sec,'_mail_code') as mail, \
             mock.patch.object(sec,'_check_code'),\
             mock.patch.object(sec,'_password_new',return_value='very strong example passphrase'),\
             mock.patch.object(sec,'_create_recovery_codes',return_value=[]):
            sec.setup()
            mail.assert_called_once_with('valid@example.org')
            self.assertTrue(sec._read(sec.STATE)['email_verified'])


class PTYPromptTests(unittest.TestCase):
    def test_menu_then_text_then_masked_password_then_text(self):
        child, fd = pty.fork()
        if child == 0:
            try:
                from menu import pick
                assert pick('AUTOKOM AUTH',['Configurer SMTP'],cancel_label='Quitter') == 1
                name=sec._tty_text('EMAIL_HOST')
                password=sec._tty_secret('EMAIL_MDP')
                address=sec._tty_text('EMAIL_ADMIN')
                os.write(1,('\nAUTOKOM_PTY_OK:'+name+'|'+str(len(password))+'|'+address+'\n').encode())
                os._exit(0)
            except BaseException as e:
                os.write(1,('\nAUTOKOM_PTY_FAIL:'+repr(e)+'\n').encode())
                os._exit(3)
        try:
            prompts=[(b'AUTOKOM AUTH',b'1'), (b'EMAIL_HOST',b'smtp.example.org\r'),
                     (b'EMAIL_MDP',b'private-example-password\r'),
                     (b'EMAIL_ADMIN',b'admin@example.org\r')]
            seen=b''
            pos=0
            deadline=time.monotonic()+9
            while time.monotonic()<deadline:
                ready,_,_=select.select([fd],[],[],.25)
                if ready:
                    try:seen+=os.read(fd,8192)
                    except OSError:break
                    if pos<len(prompts) and prompts[pos][0] in seen:
                        os.write(fd,prompts[pos][1]);pos+=1
                    if b'AUTOKOM_PTY_OK' in seen or b'AUTOKOM_PTY_FAIL' in seen:break
            _,status=os.waitpid(child,0)
            self.assertEqual(os.waitstatus_to_exitcode(status),0,seen.decode(errors='replace')[-1300:])
            self.assertIn(b'AUTOKOM_PTY_OK:smtp.example.org|24|admin@example.org',seen)
            self.assertNotIn(b'private-example-password',seen)
        finally:
            os.close(fd)

if __name__=='__main__':unittest.main()
