"""AUTOKOM billing mode: exercise the public CLI and safe env persistence.

No root, systemd or production files are touched by these tests.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_app_billing_mode as mode
import autokom_app_manager as manager
import autokom_cli as cli


class BillingModes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'backend.env'
        self.path.write_text('DJANGO_SECRET_KEY="sensitive-secret"\nAUTOKOM_BILLING_SANDBOX_ENABLED="false"\n')
        self.path.chmod(0o600)
        self.lock = self.root / 'locks' / 'mode.lock'
        self.started = []
        patches = [
            patch.object(mode, 'LOCK', self.lock),
            patch.object(mode, 'file_path', return_value=self.path),
            patch.object(mode, 'require_root'),
            patch.object(mode, 'backup', return_value=self.root / 'backup'),
            patch.object(mode.os, 'isatty', return_value=True),
            patch.object(mode, '_run', return_value=SimpleNamespace(returncode=0, stdout='')),
            patch.object(mode, '_restart_and_check', side_effect=self.started.append),
            patch.object(mode, 'write_env', side_effect=self._write_env),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_env(self, path, values):
        path.write_text(''.join(f'{k}="{v}"\n' for k, v in sorted(values.items())))
        path.chmod(0o600)

    def test_test_mode_prompts_if_no_account_and_is_persistent(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mode.switch('test', prompt=lambda _: 'Test@Example.com'), 0)
        self.assertIn('AUTOKOM_BILLING_SANDBOX_ENABLED="true"', self.path.read_text())
        self.assertIn('AUTOKOM_BILLING_SANDBOX_TEST_EMAILS="test@example.com"', self.path.read_text())
        self.assertIn('MODE TEST', out.getvalue())
        self.assertNotIn('test@example.com', out.getvalue())
        self.assertEqual(self.started, ['true'])
        self.assertIn('DJANGO_SECRET_KEY="sensitive-secret"', self.path.read_text())

    def test_prod_mode_disables_billing_but_preserves_history_and_allowlist(self):
        self.path.write_text('AUTOKOM_BILLING_SANDBOX_ENABLED="true"\nAUTOKOM_BILLING_SANDBOX_TEST_EMAILS="test@example.com"\n')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(mode.switch('prod'), 0)
        self.assertIn('AUTOKOM_BILLING_SANDBOX_ENABLED="false"', self.path.read_text())
        self.assertIn('AUTOKOM_BILLING_SANDBOX_TEST_EMAILS="test@example.com"', self.path.read_text())
        self.assertEqual(self.started, ['false'])

    def test_invalid_email_cannot_enable(self):
        with self.assertRaises(mode.BillingModeError):
            mode.switch('test', prompt=lambda _: '*')
        self.assertNotIn('SANDBOX_TEST_EMAILS', self.path.read_text())
        self.assertEqual(self.started, [])

    def test_non_tty_without_allowlist_fails_closed(self):
        with patch.object(mode.os, 'isatty', return_value=False):
            with self.assertRaises(mode.BillingModeError):
                mode.switch('test')
        self.assertEqual(self.started, [])

    def test_restart_failure_rolls_back_previous_values(self):
        def fail_first(value):
            self.started.append(value)
            if len(self.started) == 1:
                raise mode.BillingModeError('restart failed')
        with patch.object(mode, '_restart_and_check', side_effect=fail_first):
            with self.assertRaisesRegex(mode.BillingModeError, 'restaurée'):
                mode.switch('test', prompt=lambda _: 'test@example.com')
        self.assertIn('AUTOKOM_BILLING_SANDBOX_ENABLED="false"', self.path.read_text())
        self.assertNotIn('SANDBOX_TEST_EMAILS', self.path.read_text())
        self.assertEqual(self.started, ['true', 'false'])

    def test_read_only_status_does_not_restart(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(mode.status(), 0)
        self.assertIn('PRODUCTION', output.getvalue())
        self.assertEqual(self.started, [])

    def test_symlink_backend_env_is_rejected(self):
        self.path.unlink()
        real = self.root / 'real.env'
        real.write_text('AUTOKOM_BILLING_SANDBOX_ENABLED="false"\n')
        self.path.symlink_to(real)
        with self.assertRaises(mode.BillingModeError):
            mode.switch('prod')
        self.assertEqual(self.started, [])

    def test_both_flags_rejected_by_argparse(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            manager.main(['--mode-prod', '--mode-test'])

    def test_cli_dispatch_activates_global_passphrase_for_mutation(self):
        # Actual authorization must happen before calling the app manager.
        import autokom_security
        with patch.object(autokom_security, 'configured', return_value=True), \
             patch.object(autokom_security, 'authorize') as authorize, \
             patch.object(manager, 'main', return_value=0) as delegate:
            cli.main(['app', '--mode-test'])
            authorize.assert_called_once()
            delegate.assert_called_once_with(['--mode-test'])


if __name__ == '__main__':
    unittest.main()
