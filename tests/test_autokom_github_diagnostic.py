"""Diagnostics AUTOKOM sans accès à Django, ni aux secrets AWS."""
import unittest
from unittest.mock import patch

from autokom_github_doctor import diagnose, fernet_key_shape_ok, callback_ok

class GitHubAppDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.backend = dict(GITHUB_APP_CLIENT_ID='Iv1.test', GITHUB_APP_CLIENT_SECRET='test-placeholder',
            GITHUB_APP_SLUG='autokom-deploy', GITHUB_APP_CALLBACK_URL='https://backend.auto.ikom.mg/api/projects/git/github/callback/',
            AUTOKOM_FIELD_ENCRYPTION_KEY='YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU=',
            FRONTEND_URL='https://auto.ikom.mg', GITHUB_WEBHOOK_SECRET='set')
        self.frontend = dict(VITE_AUTOKOM_API_ENABLED='true', VITE_AUTOKOM_FRONTEND_ONLY='false',
            VITE_AUTOKOM_GITHUB_ENABLED='true', FRONTEND_URL='https://auto.ikom.mg',
            BACKEND_URL='https://backend.auto.ikom.mg')

    def test_well_formed_config_announces_both_callbacks_without_secret(self):
        good, warnings, errors = diagnose(self.backend, self.frontend)
        self.assertFalse(errors)
        self.assertTrue(any('api/auth/github/callback/' in x for x in good))
        self.assertTrue(any('api/projects/git/github/callback/' in x for x in good))
        self.assertNotIn(self.backend['GITHUB_APP_CLIENT_SECRET'], repr(good+warnings+errors))

    def test_secret_presence_does_not_prove_its_validity(self):
        self.backend['AUTOKOM_FIELD_ENCRYPTION_KEY'] = 'malformed'
        _, _, errors = diagnose(self.backend, self.frontend)
        self.assertTrue(any('Fernet' in x for x in errors))

    def test_refuse_callback_wrong_scheme_domain_username_or_path(self):
        for url in ['http://backend.auto.ikom.mg/api/auth/github/callback/',
                    'https://user:pass@backend.auto.ikom.mg/api/auth/github/callback/',
                    'https://backend.auto.ikom.mg/api/other/']:
            self.assertFalse(callback_ok(url, '/api/auth/github/callback/'))

    def test_filtered_logs_hide_oauth_codes_and_states(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from autokom_app_manager import github_oauth_logs
        journal = ('GET /api/projects/git/github/callback/?code=private-code&state=private-state HTTP/1.1\n'
                   'AUTOKOM GitHub repositories OAuth : échec à l’étape exchange (catégorie GitHubReconnectRequired)\n')
        output = io.StringIO()
        with patch('autokom_app_manager.os.geteuid', return_value=0), \
             patch('autokom_app_manager.load_state', return_value={'runtime':'systemd'}), \
             patch('autokom_app_manager.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=journal)), \
             redirect_stdout(output):
            self.assertEqual(github_oauth_logs(30), 0)
        self.assertIn('échec à l’étape exchange', output.getvalue())
        self.assertNotIn('private-code', output.getvalue())
        self.assertNotIn('private-state', output.getvalue())

    def test_frontend_flags_are_warnings_not_false_oauth_success(self):
        self.frontend['VITE_AUTOKOM_GITHUB_ENABLED']='false'
        _, warnings, errors=diagnose(self.backend, self.frontend)
        self.assertFalse(errors)
        self.assertTrue(any('VITE_AUTOKOM_GITHUB_ENABLED' in x for x in warnings))

if __name__ == '__main__':
    unittest.main()
