import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


manager = load(BASE / 'scripts/lib/autokom_app_manager.py', 'manager_test_fix')
updater = load(BASE / 'scripts/lib/autokom_private_update.py', 'updater_test_fix')


class MysqlTests(unittest.TestCase):
    def test_check_runs_django_in_actual_app_environment(self):
        with patch.object(manager, 'load_dotenv', return_value={
            'DB_HOST': '127.0.0.1', 'DB_NAME': 'autokom_platform',
            'DB_USER': 'autokom', 'DB_PASSWORD': 'not-a-real-secret',
        }), patch.object(manager, 'run_as_service') as run:
            manager.verify_mysql_service_access()
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:4], ['manage.py', 'shell', '-c'])
        self.assertIn('SELECT 1', argv[-1])
        self.assertNotIn('not-a-real-secret', ' '.join(argv))

    def test_missing_password_is_refused(self):
        with patch.object(manager, 'load_dotenv', return_value={
            'DB_HOST': '127.0.0.1', 'DB_NAME': 'autokom_platform', 'DB_USER': 'autokom',
        }):
            with self.assertRaisesRegex(manager.AppError, 'DB_PASSWORD'):
                manager.verify_mysql_service_access()

    def test_mysql_fail_is_actionable_and_doesnt_print_secret(self):
        with patch.object(manager, 'load_dotenv', return_value={
            'DB_HOST': '127.0.0.1', 'DB_NAME': 'autokom_platform',
            'DB_USER': 'autokom', 'DB_PASSWORD': 'highly-secret-123',
        }), patch.object(manager, 'run_as_service', side_effect=manager.AppError('Erreur de connexion')):
            with self.assertRaises(manager.AppError) as ctx:
                manager.verify_mysql_service_access()
        self.assertNotIn('highly-secret-123', str(ctx.exception))
        self.assertIn('/etc/autokom/platform-backend.env', str(ctx.exception))

    def test_debug_pipeline_detects_migration_missing(self):
        source = (BASE / 'scripts/lib/autokom_app_manager.py').read_text()
        self.assertIn("'makemigrations', '--check', '--dry-run'", source)
        self.assertIn('def doctor(args):', source)
        self.assertIn('verify_mysql_service_access()', source)
        self.assertIn('def _last_lines(self):', source)


class GitChangeTests(unittest.TestCase):
    def test_git_changes_real_added_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def run(*args):
                subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
            run('init', '-q')
            run('config', 'user.email', 'tests@example.invalid')
            run('config', 'user.name', 'AUTOKOM Tests')
            (root/'code.py').write_text('one\ntwo\n')
            run('add', '.')
            run('commit', '-qm', 'before')
            old = updater._git(root, 'rev-parse', 'HEAD')
            (root/'code.py').write_text('one\nthree\nextra\n')
            (root/'another.py').write_text('hello\n')
            run('add', '.')
            run('commit', '-qm', 'after')
            new = updater._git(root, 'rev-parse', 'HEAD')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                updater._print_git_changes(root, old, new)
            msg = out.getvalue()
            self.assertIn('code.py', msg)
            self.assertIn('another.py', msg)
            self.assertIn('+3', msg)
            self.assertIn('-1', msg)
            self.assertIn('origin/main', msg)


if __name__ == '__main__':
    unittest.main()
