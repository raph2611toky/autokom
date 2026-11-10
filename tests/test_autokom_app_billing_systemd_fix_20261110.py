"""Regression for legacy systemd env paths and Gunicorn /proc environ mutation."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, call

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_app_billing_mode as billing


def result(code=0, output=''):
    return SimpleNamespace(returncode=code, stdout=output, stderr='')


class BillingServiceIntegration(unittest.TestCase):
    def test_environment_file_is_recognized_without_touching_secret_values(self):
        with patch.object(billing, '_run', return_value=result(0,
                '/etc/autokom/secrets/backend.env (ignore_errors=no)')):
            self.assertEqual(billing._loaded_environment_files(), ['/etc/autokom/secrets/backend.env'])

    def test_legacy_backend_env_is_repaired_and_daemon_reloaded(self):
        replies = [result(0, '/etc/autokom/platform-backend.env (ignore_errors=no)'),
                   result(), result(0, '/etc/autokom/secrets/backend.env (ignore_errors=no)')]
        with patch.object(billing, '_run', side_effect=replies) as runner, \
             patch.object(billing, '_systemd_override', return_value=True) as override, \
             patch.object(billing, 'file_path', return_value=Path('/etc/autokom/secrets/backend.env')):
            billing._ensure_service_env()
        override.assert_called_once_with('autokom-backend', Path('/etc/autokom/secrets/backend.env'))
        self.assertIn('daemon-reload', runner.call_args_list[1].args)

    def test_foreign_later_override_fails_closed(self):
        replies = [result(0, '/etc/autokom/other.env (ignore_errors=no)'),
                   result(), result(0, '/etc/autokom/secrets/backend.env (ignore_errors=no) /etc/autokom/other.env (ignore_errors=no)')]
        with patch.object(billing, '_run', side_effect=replies), \
             patch.object(billing, '_systemd_override', return_value=True), \
             patch.object(billing, 'file_path', return_value=Path('/etc/autokom/secrets/backend.env')):
            with self.assertRaisesRegex(billing.BillingModeError, 'overrides'):
                billing._ensure_service_env()

    def test_mode_is_validated_by_django_via_systemd_without_printing_env(self):
        with patch.object(billing, '_run', return_value=result()) as runner:
            billing._django_env_probe('true')
        args = runner.call_args.args
        self.assertEqual(args[0], '/usr/bin/systemd-run')
        self.assertIn('--property=EnvironmentFile=/etc/autokom/secrets/backend.env', args)
        self.assertIn('--property=User=autokom', args)
        self.assertEqual(args[-1], 'true')
        self.assertNotIn('DJANGO_SECRET_KEY', ' '.join(args))

    def test_probe_failure_stops_activation(self):
        with patch.object(billing, '_run', return_value=result(3)):
            with self.assertRaisesRegex(billing.BillingModeError, 'Vérification Django'):
                billing._django_env_probe('true')

    def test_restart_does_not_use_mutable_proc_environ(self):
        replies = [result(), result(), result(0, '12345\n')]
        with patch.object(billing, '_ensure_service_env'), \
             patch.object(billing, '_run', side_effect=replies), \
             patch.object(billing, '_django_env_probe') as probe, \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('do not read /proc/PID/environ')):
            billing._restart_and_check('true')
        probe.assert_called_once_with('true')


if __name__ == '__main__':
    unittest.main()
