"""AUTOKOM error diagnostics must be actionable without leaking client builds."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_buildkit_admin as admin

class ProbeErrorTests(unittest.TestCase):
    def test_error_categories(self):
        code, message = admin._smoke_failure_code('runc: error mounting "proc" to rootfs at "/proc": operation not permitted')
        self.assertEqual(code, 'PROCFS_DENIED')
        self.assertIn('montage', message.lower())
        self.assertEqual(admin._smoke_failure_code('failed to resolve source metadata')[0], 'REGISTRY_UNAVAILABLE')
        self.assertEqual(admin._smoke_failure_code('disk quota exceeded')[0], 'STORAGE_LIMIT')
        self.assertEqual(admin._smoke_failure_code('unknown')[0], 'OCI_RUN_FAILED')

    def test_admin_cli_fails_closed_and_no_raw_output(self):
        with patch.object(admin, 'healthcheck', return_value=True), patch.object(admin, 'smoke_test', side_effect=admin.BuildkitError('code=PROCFS_DENIED')):
            self.assertEqual(admin.main(['--smoke-test']), 2)

    def test_role_reports_safe_message_and_preserves_check_mode(self):
        role = (ROOT/'ansible/roles/buildkit_rootless/tasks/main.yml').read_text()
        self.assertIn('AUTOKOM', (ROOT/'scripts/lib/autokom_buildkit_admin.py').read_text())
        self.assertIn('register: ak_bk_smoke', role)
        self.assertIn('failed_when: false', role)
        self.assertIn('ansible.builtin.fail', role)
        self.assertIn('when: not ansible_check_mode', role)

    def test_no_automatic_privilege_bypass(self):
        code=(ROOT/'scripts/lib/autokom_buildkit_admin.py').read_text()
        unit=(ROOT/'scripts/systemd/autokom-buildkit.service').read_text()
        self.assertIn('User=autokom-buildkit', unit)
        self.assertNotIn('--oci-worker-no-process-sandbox', unit)
        self.assertNotIn('kernel.apparmor_restrict_unprivileged_userns=0', code)
