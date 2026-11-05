"""Regression guards for production GitHub App enablement (offline, no secrets)."""
import sys
import io
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import autokom_env_manager as envm
import autokom_frontend_manager as fm
import autokom_app_manager as appm


class GitHubEnablementTests(unittest.TestCase):
    base_env = {
        'VITE_AUTOKOM_FRONTEND_ONLY': 'false',
        'VITE_AUTOKOM_API_ENABLED': 'true',
        'VITE_AUTOKOM_GITHUB_ENABLED': 'true',
        'VITE_AUTOKOM_API_BASE': '/api',
        'VITE_AUTOKOM_AUTH_MODE': 'cookie',
        'FRONTEND_URL': 'https://auto.ikom.mg',
        'BACKEND_URL': 'https://backend.auto.ikom.mg',
    }

    def test_production_real_github_mode_is_accepted(self):
        envm._validate('frontend', self.base_env)
        with mock.patch.object(envm, 'read_env', return_value=self.base_env):
            settings = fm.build_environment(Path('/unused'))
        self.assertEqual(settings['VITE_AUTOKOM_GITHUB_ENABLED'], 'true')
        self.assertEqual(settings['VITE_AUTOKOM_API_ENABLED'], 'true')

    def test_github_never_enables_in_static_demo(self):
        for overrides in ({'VITE_AUTOKOM_FRONTEND_ONLY': 'true'},
                          {'VITE_AUTOKOM_API_ENABLED': 'false'}):
            values = {**self.base_env, **overrides}
            with self.subTest(values=overrides):
                with self.assertRaises(envm.EnvError):
                    envm._validate('frontend', values)
                with mock.patch.object(envm, 'read_env', return_value=values):
                    with self.assertRaises(fm.FrontendError):
                        fm.build_environment(Path('/unused'))

    def test_github_defaults_disabled_and_flag_is_fail_closed(self):
        no_flag = {k: v for k, v in self.base_env.items() if k != 'VITE_AUTOKOM_GITHUB_ENABLED'}
        with mock.patch.object(envm, 'read_env', return_value=no_flag):
            result = fm.build_environment(Path('/unused'))
        self.assertEqual(result['VITE_AUTOKOM_GITHUB_ENABLED'], 'false')
        runtime = (ROOT / 'platform/frontend/src/config/runtime.js').read_text()
        self.assertIn("VITE_AUTOKOM_GITHUB_ENABLED === 'true'", runtime)

    def test_runner_key_and_optional_login_callback_can_reach_django_service(self):
        self.assertIn('AUTOKOM_RUNNER_SHARED_KEY', appm.ALLOWED_ENV_KEYS)
        self.assertIn('GITHUB_AUTH_CALLBACK_URL', appm.ALLOWED_ENV_KEYS)
        envm._validate('backend', {'AUTOKOM_RUNNER_SHARED_KEY': 'X' * 48,
                                 'GITHUB_AUTH_CALLBACK_URL': 'https://backend.auto.ikom.mg/api/auth/github/callback/'})
        with self.assertRaises(envm.EnvError):
            envm._validate('backend', {'AUTOKOM_RUNNER_SHARED_KEY': 'short'})
        with self.assertRaises(envm.EnvError):
            envm._validate('backend', {'GITHUB_AUTH_CALLBACK_URL': 'http://bad.example/api/auth/github/callback/'})

    def test_runner_sync_copies_without_printing_the_secret(self):
        from pathlib import Path
        shared = 'secret-only-in-fixtures-' + 'k' * 42
        def path(kind):
            return Path('/example/' + kind + '.env')
        def read(path):
            if str(path).endswith('/runtime.env'):
                return {'AUTOKOM_RUNNER_SHARED_KEY': shared}
            return {'DB_HOST': '127.0.0.1'}
        sink = io.StringIO()
        with mock.patch.object(envm, 'require_root'), mock.patch.object(envm, 'file_path', side_effect=path), \
             mock.patch.object(envm, 'read_env', side_effect=read), mock.patch.object(envm, 'backup'), \
             mock.patch.object(envm, 'write_env') as writer, mock.patch.object(Path, 'is_file', return_value=False), \
             redirect_stdout(sink):
            self.assertEqual(envm.sync_runner_key(), 0)
        written = writer.call_args.args[1]
        self.assertEqual(written['AUTOKOM_RUNNER_SHARED_KEY'], shared)
        self.assertNotIn(shared, sink.getvalue())

    def test_runner_sync_rejects_mismatched_backend_key(self):
        shared = 'x' * 48
        with mock.patch.object(envm, 'require_root'), mock.patch.object(envm, 'file_path', side_effect=lambda kind: Path('/test/' + kind)), \
             mock.patch.object(envm, 'read_env', side_effect=[{'AUTOKOM_RUNNER_SHARED_KEY':shared},
                 {'AUTOKOM_RUNNER_SHARED_KEY':'y' * 48}]):
            with self.assertRaises(envm.EnvError):
                envm.sync_runner_key()

    def test_runner_sync_is_protected_by_cli_passphrase_gate(self):
        cli = (ROOT / 'scripts/lib/autokom_cli.py').read_text()
        self.assertIn("'sync-runner-key'", cli)
        self.assertIn("'sync-runner-key'", (ROOT / 'scripts/lib/autokom_env_manager.py').read_text())

    def test_git_status_contract_does_not_return_credentials(self):
        src = (ROOT / 'platform/backend/apps/projects/git/views.py').read_text()
        self.assertIn("'configured': bool(configured)", src)
        self.assertIn("'connected': bool(account) and bool(configured)", src)
        self.assertNotIn("'access_token':", src)
        self.assertNotIn("'client_secret':", src)

    def test_frontend_connector_is_real_and_no_demo_repo_fallback(self):
        src = (ROOT / 'platform/frontend/src/components/LiveGitHubConnector.jsx').read_text()
        self.assertIn('gitApi.installations()', src)
        self.assertIn('backendConfigured', src)
        self.assertIn('github.com', src)
        self.assertNotIn('getSampleDirectory', src)


if __name__ == '__main__':
    unittest.main()
