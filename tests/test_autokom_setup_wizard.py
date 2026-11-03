"""Tests isolated from root, network, MySQL, Docker, Nginx and Ansible."""
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout

HERE = Path(__file__).resolve().parents[1]
MODULE_PATH = HERE/'scripts/lib/autokom_app_manager.py'
spec = importlib.util.spec_from_file_location('autokom_app_setup_tests', MODULE_PATH)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.backend = Path(self._tmp.name) / 'backend'
        self.backend.mkdir()
        (self.backend/'manage.py').write_text('# test')
        self._patch = mock.patch.object(app, 'BACKEND', self.backend)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_domain_security(self):
        self.assertEqual(app.validate_domain('API.IK0M.TECH.'), 'api.ik0m.tech')
        for host in ('localhost', '*.ik0m.tech', 'bad.ik0m.tech; rm -rf /', 'bad..example.com'):
            with self.subTest(host=host), self.assertRaises(app.AppError):
                app.validate_domain(host)

    def test_flags_show_default_runtime_and_interactive_mode(self):
        with self.assertRaises(SystemExit) as e, redirect_stdout(io.StringIO()) as out:
            app.main(['setup', '-h'])
        self.assertEqual(e.exception.code, 0)
        for name in ('--runtime', '--environment', '--env-file', '--check', '--nginx-mode', '--tls-mode', '--workers'):
            self.assertIn(name, out.getvalue())

    def test_env_has_legacy_keys_and_fails_on_unexpected(self):
        with tempfile.TemporaryDirectory() as folder:
            outfile = Path(folder)/'platform-backend.env'
            data = {
                'DEBUG': 'False', 'DB_ENGINE': 'mysql',
                'DB_NAME':'autokom_platform', 'DB_USER':'autokom', 'DB_PASSWORD':'not-output',
                'CLIENT_JWT_ISSUER': 'autokom-platform', 'CLIENT_JWT_AUDIENCE': 'autokom-clients',
                'CLIENT_ACCESS_MINUTES': '15', 'CLIENT_REFRESH_DAYS':'7',
                'DB_TEST_NAME': 'test_autokom_platform',
                'EMAIL_USE_SSL': 'False', 'AES_IV': 'existingiv', 'AES_KEY':'existingkey',
            }
            with mock.patch.object(app, 'CONFIG_DIR', Path(folder)), mock.patch.object(app, 'ENV_FILE', outfile):
                app.secure_env_write(data)
                loaded = app.load_dotenv(outfile)
                self.assertEqual(loaded['AES_IV'], 'existingiv')
                self.assertEqual(loaded['CLIENT_JWT_AUDIENCE'], 'autokom-clients')
                self.assertEqual(outfile.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(app.AppError):
                    app.secure_env_write({**data, 'LD_PRELOAD': '/tmp/fake.so'})

    def test_setup_check_does_not_run_privileged_calls(self):
        data = {
            'DEBUG': 'True','DB_ENGINE':'mysql', 'DB_HOST':'127.0.0.1','DB_PORT':'3306',
            'DB_NAME':'autokom_platform','DB_USER':'autokom','DB_PASSWORD':'password',
            'EMAIL_HOST':'smtp-relay.brevo.com','EMAIL_HOST_USER':'account',
            'EMAIL_HOST_PASSWORD':'password','EMAIL_BACKEND':'django.core.mail.backends.smtp.EmailBackend',
            'DJANGO_SECRET_KEY':'secret','CLIENT_JWT_SIGNING_KEY':'jwtsecret',
        }
        with mock.patch.object(app, '_candidate_env', return_value=Path('/etc/autokom/secrets/backend.env')), \
             mock.patch.object(app, 'load_dotenv', return_value=data), \
             mock.patch.object(app, '_safe_env_check'), \
             mock.patch.object(app, 'STATE_FILE', Path('/nonexistent/autokom-state-file.json')), \
             mock.patch.object(app, 'root_only') as root_only, \
             mock.patch.object(app, '_install_os_requirements') as install_deps, \
             mock.patch.object(app, 'write_nginx') as write_nginx, \
             mock.patch.object(app, 'secure_env_write') as write_env, \
             mock.patch.object(app, 'create_user') as create_user, \
             redirect_stdout(io.StringIO()) as out:
            result = app.main(['setup', '--domain', 'backend.auto.ik0m.tech',
                '--frontend', 'https://auto.ik0m.tech','--email', 'admin@ik0m.tech',
                '--runtime','systemd','--check'])
            self.assertEqual(result, 0)
            self.assertIn('aucune modification', out.getvalue())
            for call in (root_only, install_deps, write_nginx, write_env, create_user):
                call.assert_not_called()

    def test_docker_local_db_is_rejected(self):
        from argparse import Namespace
        opts = Namespace(runtime='docker', environment='production', domain='backend.auto.ik0m.tech',
            email='admin@ik0m.tech', frontend='https://auto.ik0m.tech', port=8172, workers=2,
            provision_db=False, nginx_mode='managed', tls_mode='certbot')
        settings = {'DB_NAME':'db','DB_USER':'app','DB_PASSWORD':'pw', 'DB_HOST':'127.0.0.1',
            'EMAIL_BACKEND':'django.core.mail.backends.smtp.EmailBackend',
            'EMAIL_HOST':'smtp','EMAIL_HOST_USER':'user','EMAIL_HOST_PASSWORD':'pw',
            'DJANGO_SECRET_KEY':'secret','CLIENT_JWT_SIGNING_KEY':'secret'}
        with mock.patch.object(app, '_safe_env_check'):
            with self.assertRaisesRegex(app.AppError, 'Docker'):
                app._preflight(opts, settings, Path('/etc/autokom/secrets/backend.env'))

    def test_required_preprod_tls(self):
        from argparse import Namespace
        opts = Namespace(runtime='systemd', environment='production', domain='backend.auto.ik0m.tech',
            email='admin@ik0m.tech', frontend='https://auto.ik0m.tech', port=8172, workers=2,
            provision_db=False, nginx_mode='managed', tls_mode='none')
        settings = {'DB_NAME':'db','DB_USER':'app','DB_PASSWORD':'pw', 'DB_HOST':'127.0.0.1',
            'EMAIL_BACKEND':'django.core.mail.backends.smtp.EmailBackend',
            'EMAIL_HOST':'smtp','EMAIL_HOST_USER':'user','EMAIL_HOST_PASSWORD':'pw',
            'DJANGO_SECRET_KEY':'secret','CLIENT_JWT_SIGNING_KEY':'secret'}
        with mock.patch.object(app, '_safe_env_check'):
            with self.assertRaisesRegex(app.AppError, 'HTTPS'):
                app._preflight(opts, settings, Path('/etc/autokom/secrets/backend.env'))


if __name__ == '__main__':
    unittest.main()
