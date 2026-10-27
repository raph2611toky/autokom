#!/usr/bin/env python3
"""Jenkins onboarding: three attempts, no destructive changes or credential disclosure."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('auth_retry',ROOT/'scripts/lib/jenkins_postinstall.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
URL='https://jenkins.example.org/'


class RetryTests(unittest.TestCase):
    def args(self): return m.parse(['--apply','--interactive'])

    def test_retry_empty_token_then_valid(self):
        with mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',side_effect=['admin','admin']), \
             mock.patch.object(m,'read_masked',side_effect=[ValueError('Secret vide ou invalide'),'VALID_TOKEN']), \
             mock.patch.object(m,'verify_admin') as verifier:
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                with m.authorization_context(self.args(), URL, apply=True) as path:
                    self.assertEqual(Path(path).read_text(), 'admin:VALID_TOKEN\n')
                self.assertFalse(Path(path).exists())
            self.assertEqual(verifier.call_count,1)
            self.assertIn('2 essai(s) restant(s)',out.getvalue())
            self.assertNotIn('VALID_TOKEN',out.getvalue())

    def test_retry_wrong_username_then_valid(self):
        with mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',side_effect=['token-looking-string','admin']), \
             mock.patch.object(m,'read_masked',side_effect=['INVALID_TOKEN','VALID_TOKEN']), \
             mock.patch.object(m,'verify_admin',side_effect=[m.AuthenticationRejected('Mauvais compte'),None]) as check:
            with m.authorization_context(self.args(),URL,apply=True) as path:
                self.assertEqual(Path(path).read_text(),'admin:VALID_TOKEN\n')
            self.assertFalse(Path(path).exists())
            self.assertEqual(check.call_count,2)

    def test_three_auth_failures_exit_without_yield(self):
        out=io.StringIO()
        with mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',return_value='admin') as ask, \
             mock.patch.object(m,'read_masked',return_value='INVALID_SECRET') as prompt, \
             mock.patch.object(m,'verify_admin',side_effect=m.AuthenticationRejected('Refusé')) as verify, \
             contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(RuntimeError,'après 3 essais'):
                with m.authorization_context(self.args(),URL,apply=True):
                    self.fail('Ne doit jamais donner accès sans authentification')
        self.assertEqual((ask.call_count,prompt.call_count,verify.call_count),(3,3,3))
        self.assertIn('2 essai(s) restant(s)',out.getvalue())
        self.assertIn('1 essai(s) restant(s)',out.getvalue())
        self.assertNotIn('INVALID_SECRET',out.getvalue())

    def test_unknown_remote_failure_not_retried(self):
        with mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',return_value='admin') as ask, \
             mock.patch.object(m,'read_masked',return_value='VALID_TOKEN'), \
             mock.patch.object(m,'verify_admin',side_effect=RuntimeError('Serveur injoignable')):
            with self.assertRaisesRegex(RuntimeError, 'Serveur injoignable'):
                with m.authorization_context(self.args(),URL,apply=True): pass
        self.assertEqual(ask.call_count,1)

    def test_check_mode_never_asks(self):
        args=m.parse(['--check','--interactive'])
        with mock.patch.object(m,'ensure_cli_jar') as jar, mock.patch.object(m,'ask') as ask:
            with m.authorization_context(args,URL,apply=False) as path:
                self.assertEqual(path,'')
            jar.assert_not_called(); ask.assert_not_called()

    def test_auth_file_once_no_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'token'; f.write_text('admin:TOKEN\n'); f.chmod(0o600)
            args=m.parse(['--apply','--auth-file',str(f)])
            with mock.patch.object(m,'secure_file',return_value=f), mock.patch.object(m,'ensure_cli_jar'), \
                 mock.patch.object(m,'verify_admin') as validator, mock.patch.object(m,'ask') as ask:
                with m.authorization_context(args,URL,apply=True) as path:
                    self.assertEqual(path,str(f))
                validator.assert_called_once_with(str(f)); ask.assert_not_called()

    def test_cli_identity_and_permissions_confirmed(self):
        with mock.patch.object(m,'cli',side_effect=['Authenticated as: admin\nAuthorities:\n  authenticated\n','AUTOKOM_ADMIN_AUTHORIZED\n']) as cli:
            m.verify_admin('/root/token',expected_username='admin')
            self.assertEqual(cli.call_count,2)
            self.assertEqual(cli.call_args_list[0].args[1],['who-am-i'])

    def test_anonymous_cli_rejected_before_admin(self):
        with mock.patch.object(m,'cli',return_value='Authenticated as: anonymous\n') as cli, \
             mock.patch.object(m,'verify_rest_identity',side_effect=m.AuthenticationRejected('anonymous')):
            with self.assertRaises(m.AuthenticationRejected):
                m.verify_admin('/root/token',expected_username='admin')
            cli.assert_called_once()

    def test_identity_mismatch_before_admin(self):
        with mock.patch.object(m,'cli',return_value='Authenticated as: other-user\n') as cli:
            with self.assertRaises(m.AuthenticationRejected):
                m.verify_admin('/root/token',expected_username='admin')
            cli.assert_called_once()

    def test_403_hides_server_error(self):
        with mock.patch.object(m,'cli',side_effect=RuntimeError('HTTP 403: private-token-canary')), \
             mock.patch.object(m,'verify_rest_identity',side_effect=m.AuthenticationRejected('refusé')):
            with self.assertRaises(m.AuthenticationRejected) as ctx:
                m.verify_admin('/root/token','admin')
            self.assertNotIn('private-token-canary',str(ctx.exception))

    def test_admin_permission_denied(self):
        with mock.patch.object(m,'cli',side_effect=['Authenticated as: admin\n', RuntimeError('AccessDeniedException3')]):
            with self.assertRaises(m.AuthenticationRejected):
                m.verify_admin('/root/token',expected_username='admin')

    def test_no_plugin_install_on_bad_credentials(self):
        args=self.args(); args.harden=True; args.confirm_restart=True
        with mock.patch('os.geteuid',return_value=0), mock.patch('subprocess.run') as svc, \
             mock.patch.object(m,'jenkins_state',return_value='secure'), \
             mock.patch.object(m,'trusted_web_url',return_value=URL), \
             mock.patch.object(m,'local_plugin_ids',return_value=set()), \
             mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',return_value='admin'), \
             mock.patch.object(m,'read_masked',return_value='BAD_TOKEN'), \
             mock.patch.object(m,'verify_admin',side_effect=m.AuthenticationRejected('Refusé')), \
             mock.patch.object(m,'cli') as cli:
            svc.return_value.returncode=0
            with self.assertRaisesRegex(RuntimeError,'après 3 essais'):
                m.configure(args)
            cli.assert_not_called()

    def test_post_auth_value_error_does_not_retry(self):
        # Regression: contextmanager must not mistake plugin errors for bad credentials.
        with mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',return_value='admin') as ask, \
             mock.patch.object(m,'read_masked',return_value='VALID_TOKEN'), \
             mock.patch.object(m,'verify_admin') as verifier:
            with self.assertRaisesRegex(ValueError,'plugin failure'):
                with m.authorization_context(self.args(),URL,apply=True):
                    raise ValueError('plugin failure')
        ask.assert_called_once()
        verifier.assert_called_once()

    def test_credentials_files_deleted_after_each_failed_attempt(self):
        paths=[]
        def record(path, expected_username):
            paths.append(Path(path))
            if len(paths)<3:
                raise m.AuthenticationRejected('bad token')
        with mock.patch.object(m,'ensure_cli_jar'), \
             mock.patch.object(m,'ask',return_value='admin'), \
             mock.patch.object(m,'read_masked',side_effect=['BAD1','BAD2','GOOD']), \
             mock.patch.object(m,'verify_admin',side_effect=record):
            with m.authorization_context(self.args(),URL,apply=True) as path:
                self.assertTrue(Path(path).is_file())
                self.assertTrue(all(not p.exists() for p in paths[:2]))
        self.assertTrue(all(not p.exists() for p in paths))

    def test_additional_admin_password_confirmation_three_attempts(self):
        args=self.args(); args.admin_user='new-admin'
        from contextlib import ExitStack
        with ExitStack() as stack, mock.patch.object(m,'read_masked',side_effect=[
                'invalid', 'different', 'another-bad', 'different',
                'StrongPassword12345', 'StrongPassword12345']) as prompts:
            m.prepare_extra_admin(args,stack)
            self.assertEqual(Path(args.admin_password_file).read_text(),'StrongPassword12345\n')
            self.assertEqual(prompts.call_count,6)
        self.assertFalse(Path(args.admin_password_file).exists())

    def test_section_compact_no_logo_in_check(self):
        args=m.parse(['--check','--interactive'])
        with mock.patch.object(m,'parse',return_value=args), \
             mock.patch.object(m,'interactive_configure',return_value=args), \
             mock.patch.object(m,'configure'), mock.patch.object(m,'section') as section:
            m.main()
            section.assert_called_once()
            self.assertEqual(section.call_args.kwargs['tone'],'jenkins')

if __name__=='__main__': unittest.main()
