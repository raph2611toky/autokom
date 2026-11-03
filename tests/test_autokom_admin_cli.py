"""Tests sans accès MySQL réel : garde-fous et interface admin CLI."""
from __future__ import annotations
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import autokom_admin_manager as admin


class AdminCliTests(unittest.TestCase):
    def test_manage_py_refuses_builtin_createsuperuser(self):
        proc = subprocess.run([sys.executable, str(ROOT / 'platform/backend/manage.py'),
                               'createsuperuser', '--noinput'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('autokom app admin create', proc.stderr)

    def test_django_admin_add_and_delete_disabled(self):
        source = (ROOT / 'platform/backend/apps/accounts/admin/admin.py').read_text()
        self.assertIn('def has_add_permission', source)
        self.assertIn('def has_delete_permission', source)

    def test_admin_help(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            admin.main(['--help'])
        self.assertEqual(cm.exception.code, 0)

    def test_create_rejects_incorrect_confirmation_without_subprocess(self):
        with patch.object(admin.os, 'geteuid', return_value=0), \
             patch.object(admin, '_require_authorization'), \
             patch.object(admin, '_interactive', return_value=True), \
             patch.object(admin.getpass, 'getpass', side_effect=['onepasswordlong!', 'differentpasslong!']), \
             patch.object(admin, '_execute') as execute:
            with self.assertRaises(admin.AdminError) as cm:
                admin.main(['create', '--email', 'a@example.com'])
            self.assertIn('ne correspondent pas', str(cm.exception))
            execute.assert_not_called()

    def test_create_sends_password_via_payload_not_cli(self):
        with patch.object(admin.os, 'geteuid', return_value=0), \
             patch.object(admin, '_require_authorization'), \
             patch.object(admin, '_interactive', return_value=True), \
             patch.object(admin.getpass, 'getpass', side_effect=['aVeryStrongPassphrase#2026']*2), \
             patch.object(admin, '_execute', return_value={'ok': True, 'message': 'Créé'}) as execute:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(admin.main(['create', '--email', 'ADMIN@EXAMPLE.COM']), 0)
            self.assertEqual(execute.call_args.args[0], 'create')
            self.assertEqual(execute.call_args.args[1]['email'], 'admin@example.com')
            self.assertEqual(execute.call_args.args[1]['password'], 'aVeryStrongPassphrase#2026')

    def test_disable_requires_confirmation(self):
        with patch.object(admin.os, 'geteuid', return_value=0), \
             patch.object(admin, '_require_authorization'), \
             patch.object(admin, '_interactive', return_value=True), \
             patch.object(admin, '_confirm_account_change', return_value=False), \
             patch.object(admin, '_execute') as execute:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(admin.main(['disable', '--email', 'admin@example.com']), 0)
            execute.assert_not_called()

    def test_no_password_flag(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            admin.main(['create', '--email', 'a@example.com', '--password', 'plaintext'])
        self.assertEqual(cm.exception.code, 2)

    def test_systemd_backend_process_gets_stdin_not_argv_secret(self):
        from types import ModuleType
        fake = ModuleType('autokom_app_manager')
        fake.BACKEND = ROOT / 'platform/backend'
        fake.VENV = Path('/opt/autokom/venv')
        fake.USER = 'autokom'
        fake.AppError = RuntimeError
        fake._compose_env = lambda state: {}
        fake.django_env = lambda: {'DB_PASSWORD': 'fake'}
        fake.load_state = lambda: {'runtime': 'systemd'}
        response = type('Proc', (), {'returncode': 0, 'stdout': '{"ok": true, "message": "ok"}', 'stderr': ''})()
        with patch.dict(sys.modules, {'autokom_app_manager': fake}), \
             patch.object(admin.os, 'geteuid', return_value=0), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(subprocess, 'run', return_value=response) as run:
            self.assertTrue(admin._execute('create', {'password': 'newpassword'} )['ok'])
            argv = run.call_args.args[0]
            self.assertNotIn('newpassword', ' '.join(argv))
            self.assertIn('"password": "newpassword"', run.call_args.kwargs['input'])
            self.assertIn('runuser', argv)


if __name__ == '__main__':
    unittest.main()
