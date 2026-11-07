"""No-network checks for the protected DNS configuration and CI preflight."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import autokom_platform_broker as broker

PUBLIC = {
    'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg',
    'AUTOKOM_INGRESS_IPV4': '56.228.86.160',
}
ZONE = 'c'*32
TOKEN = 'unit-test-only-not-real-credential'


class CloudflarePrivateConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / 'cloudflare-dns.env'
        pin = patch.object(broker, 'CLOUDFLARE_CONFIG', self.config)
        pin.start()
        self.addCleanup(pin.stop)
        env = patch.dict(os.environ, PUBLIC, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def write(self, *, mode=0o600):
        self.config.write_text(
            f'CLOUDFLARE_ZONE_ID="{ZONE}"\n'
            f'CLOUDFLARE_API_TOKEN="{TOKEN}"\n'
            'CLOUDFLARE_ZONE_NAME="ikom.mg"\n', encoding='utf-8')
        self.config.chmod(mode)

    def test_missing_credentials_yield_actionable_safe_code(self):
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_DOMAIN_CLOUDFLARE_CONFIG_MISSING') as err:
            broker._cloudflare_settings()
        self.assertNotIn(TOKEN, str(err.exception))

    def test_broker_reads_root_only_config_without_systemd_restart(self):
        self.write()
        config = broker._cloudflare_settings()
        self.assertEqual(config['zone'], ZONE)
        self.assertEqual(config['token'], TOKEN)
        self.assertEqual(config['suffix'], 'auto.ikom.mg')
        public=broker.handle('domain.config.check', {})
        self.assertIs(public['ready'],True)
        self.assertEqual(public['ingress_ipv4'],'56.228.86.160')
        self.assertNotIn('token',public)

    def test_no_credential_disclosure_from_check(self):
        self.write()
        self.assertNotIn(TOKEN, repr(broker.handle('domain.config.check', {})))

    def test_refuses_insecure_mode(self):
        self.write(mode=0o644)
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_CF_SECRETS_PERMISSIONS'):
            broker._cloudflare_settings()

    def test_refuses_symlink(self):
        other = Path(self.tmp.name) / 'other.env'
        other.write_text('x', encoding='utf-8')
        self.config.symlink_to(other)
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_CF_SECRETS_PERMISSIONS'):
            broker._cloudflare_settings()

    def test_refuses_zone_not_containing_suffix(self):
        self.write()
        self.config.write_text(self.config.read_text().replace('ikom.mg', 'other.example'))
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_CF_ZONE_MISMATCH'):
            broker._cloudflare_settings()

    def test_jenkins_is_not_called_from_dns_preflight(self):
        self.write()
        with patch.object(broker, 'jenkins_http') as ci:
            broker.handle('domain.config.check', {})
            ci.assert_not_called()

    def test_backend_blocks_dispatch_before_build_and_reports_safe_code(self):
        root = Path(__file__).resolve().parents[1]
        worker = (root / 'platform/backend/apps/deployments/runs/worker.py').read_text()
        self.assertLess(worker.index("request_control('domain.config.check'"), worker.index('external_id = client.dispatch(run)'))
        self.assertIn('AUTOKOM_DOMAIN_PREFLIGHT_BLOCKED', worker)
        socket = (root / 'platform/backend/apps/deployments/runs/autokom_socket.py').read_text()
        self.assertIn('domain.config.check', socket)

if __name__ == '__main__':
    unittest.main()
