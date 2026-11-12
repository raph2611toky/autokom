"""Static integration contracts for code packaged into a source-only AUTOKOM patch.
These do not replace Django migrations, browser tests, or live GCP smoke tests.
"""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MVPContracts(unittest.TestCase):
    def test_backend_sources_are_valid_python(self):
        for name in (
            'platform/backend/apps/plans/entitlements/models.py',
            'platform/backend/apps/plans/entitlements/views.py',
            'platform/backend/apps/plans/entitlements/mvp.py',
            'platform/backend/apps/plans/entitlements/mvp_policy.py',
            'platform/backend/apps/plans/entitlements/management/commands/autokom_mvp_reconcile.py',
            'platform/backend/apps/projects/configurations/services.py',
            'scripts/lib/autokom_ha_worker_sync.py',
        ):
            with self.subTest(name=name):
                ast.parse((ROOT / name).read_text(encoding='utf-8'))

    def test_mvp_disabled_by_default(self):
        source = (ROOT / 'platform/backend/apps/plans/entitlements/mvp.py').read_text()
        self.assertIn("os.environ.get('AUTOKOM_MVP_ENABLED', '')", source)

    def test_no_automatic_purge_or_cloud_ha_withdrawal(self):
        src = (ROOT / 'platform/backend/apps/plans/entitlements/management/commands/autokom_mvp_reconcile.py').read_text()
        self.assertIn('SKIP HA project=', src)
        self.assertIn('no container deletion', src)
        self.assertNotIn("request_control('runtime.deprovision'", src)
        self.assertNotIn("request_control('domain.remove'", src)

    def test_legacy_checkout_rejected_during_mvp(self):
        src = (ROOT / 'platform/backend/apps/plans/entitlements/views.py').read_text()
        self.assertIn('if mvp_enabled():', src)
        self.assertIn('Aucun paiement requis', src)

    def test_shared_server_authorization_hook(self):
        src = (ROOT / 'platform/backend/apps/projects/configurations/services.py').read_text()
        self.assertIn('return mvp_authorize(cfg)', src)
        self.assertIn('if mvp_enabled():', src)

    def test_ha_sync_systemd_does_not_block_snap_confinement(self):
        src = (ROOT / 'scripts/lib/autokom_ha_worker_sync.py').read_text()
        self.assertIn('NoNewPrivileges=no', src)
        self.assertNotIn('NoNewPrivileges=yes', src)


if __name__ == '__main__':
    unittest.main()
