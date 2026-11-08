"""Tests rapides, sans secrets ni appels réseau, de l'activation GitHub App."""
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from autokom_github_activate import activate


class GitHubActivationTests(unittest.TestCase):
    backend = dict(GITHUB_APP_CLIENT_ID='Iv1.test', GITHUB_APP_CLIENT_SECRET='secret-not-real',
                   GITHUB_APP_SLUG='autokom-deploy',
                   GITHUB_APP_CALLBACK_URL='https://backend.auto.ikom.mg/api/projects/git/github/callback/',
                   FRONTEND_URL='https://auto.ikom.mg',
                   AUTOKOM_FIELD_ENCRYPTION_KEY='YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=')
    frontend = dict(FRONTEND_URL='https://auto.ikom.mg', BACKEND_URL='https://backend.auto.ikom.mg',
                    VITE_AUTOKOM_GITHUB_ENABLED='false', VITE_AUTOKOM_API_ENABLED='true',
                    VITE_AUTOKOM_FRONTEND_ONLY='false', VITE_AUTOKOM_AUTH_MODE='cookie')

    @patch('autokom_github_activate.require_root')
    @patch('autokom_github_activate.read_env')
    @patch('autokom_github_activate.backup')
    @patch('autokom_github_activate.write_env')
    def test_check_only_prints_new_callback_and_does_not_edit(self, write, backup, read, root):
        read.side_effect = [self.backend, self.frontend]
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(activate(apply=False), 0)
        self.assertIn('https://auto.ikom.mg/auth/github/callback', output.getvalue())
        self.assertNotIn(self.backend['GITHUB_APP_CLIENT_SECRET'], output.getvalue())
        write.assert_not_called()
        backup.assert_not_called()

    @patch('autokom_app_manager.deploy')
    @patch('autokom_frontend_manager.deploy')
    @patch('autokom_github_activate.require_root')
    @patch('autokom_github_activate.read_env')
    @patch('autokom_github_activate.backup')
    @patch('autokom_github_activate.write_env')
    def test_apply_enables_cookie_mode_and_deploys_both(self, write, backup, read, root, front_deploy, back_deploy):
        read.side_effect = [self.backend, self.frontend]
        with redirect_stdout(StringIO()):
            self.assertEqual(activate(apply=True), 0)
        self.assertTrue(back_deploy.called)
        self.assertTrue(front_deploy.called)
        updated = write.call_args.args[1]
        self.assertEqual(updated['VITE_AUTOKOM_GITHUB_ENABLED'], 'true')
        self.assertEqual(updated['VITE_AUTOKOM_AUTH_MODE'], 'cookie')

    @patch('autokom_app_manager.deploy')
    @patch('autokom_frontend_manager.deploy', side_effect=RuntimeError('build unavailable'))
    @patch('autokom_github_activate.require_root')
    @patch('autokom_github_activate.read_env')
    @patch('autokom_github_activate.backup')
    @patch('autokom_github_activate.write_env')
    def test_apply_failure_restores_environment(self, write, backup, read, root, front_deploy, back_deploy):
        read.side_effect = [self.backend, self.frontend]
        with redirect_stdout(StringIO()):
            with self.assertRaises(RuntimeError):
                activate(apply=True)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(write.call_args.args[1]['VITE_AUTOKOM_GITHUB_ENABLED'], 'false')
        self.assertEqual(backup.call_count, 2)


if __name__ == '__main__':
    unittest.main()
