"""Prevent late Cloudflare suffix rejection after a successful Jenkins build.

Tests make no real Cloudflare, Jenkins, Docker, Nginx, Certbot or DNS calls.
"""
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class RootOwnedFixture:
    """Simulate root-owned secret files so unit tests work as non-root CI."""
    def __init__(self, path):
        self.path = path
    def __getattr__(self, name):
        return getattr(self.path, name)
    def stat(self):
        original = self.path.stat()
        return SimpleNamespace(st_mode=original.st_mode, st_uid=0)

import autokom_platform_broker as broker

EXPECTED = 'portfolio.auto.ikom.mg'

class DomainSuffixPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / 'runtime.env'
        self.cf = Path(self.tmp.name) / 'cloudflare.env'
        self.patches = [
            patch.object(broker, 'RUNTIME_CONFIG', RootOwnedFixture(self.runtime)),
            patch.object(broker, 'CLOUDFLARE_CONFIG', RootOwnedFixture(self.cf)),
            patch.dict(os.environ, {}, clear=True),
        ]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.cf.write_text('CLOUDFLARE_ZONE_ID="'+ 'a'*32 +'"\n'
                           'CLOUDFLARE_API_TOKEN="unit-test-fake"\n'
                           'CLOUDFLARE_ZONE_NAME="ikom.mg"\n')
        self.cf.chmod(0o600)

    def write_runtime(self, suffix='auto.ikom.mg', email='security@ikom.mg'):
        self.runtime.write_text('AUTOKOM_MANAGED_DOMAIN_SUFFIX="'+ suffix + '"\n'
                                'AUTOKOM_INGRESS_IPV4="56.228.86.160"\n'
                                'AUTOKOM_ACME_EMAIL="'+email+'"\n')
        self.runtime.chmod(0o600)

    def test_late_failure_is_prevented_before_dispatch(self):
        self.write_runtime(suffix='apps.ikom.mg')
        with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_DOMAIN_SUFFIX_MISMATCH'):
            broker.handle('domain.config.check',{
                'managed_suffix':'auto.ikom.mg','managed_hosts':[EXPECTED]})

    def test_hostname_must_be_strict_child_of_allowed_suffix(self):
        self.write_runtime()
        checked=broker.handle('domain.config.check',{
            'managed_suffix':'auto.ikom.mg','managed_hosts':[EXPECTED]})
        self.assertTrue(checked['ready'])
        self.assertEqual(checked['managed_suffix'],'auto.ikom.mg')
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_DOMAIN_SUFFIX_MISMATCH'):
            broker.handle('domain.config.check',{
                'managed_suffix':'auto.ikom.mg','managed_hosts':['portfolio.auto.ikom.mg.attacker.net']})

    def test_settings_change_is_read_without_broker_restart(self):
        self.write_runtime(suffix='apps.ikom.mg')
        with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_DOMAIN_SUFFIX_MISMATCH'):
            broker.handle('domain.config.check',{'managed_hosts':[EXPECTED]})
        self.write_runtime(suffix='auto.ikom.mg')
        self.assertTrue(broker.handle('domain.config.check',{'managed_hosts':[EXPECTED]})['ready'])

    def test_public_ip_and_certbot_email_are_checked_before_build(self):
        self.write_runtime(email='')
        with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_ACME_EMAIL_MISSING'):
            broker.handle('domain.config.check',{'managed_hosts':[EXPECTED]})

    def test_runtime_secret_config_must_not_be_world_readable(self):
        self.write_runtime()
        self.runtime.chmod(0o644)
        with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_RUNTIME_ENV_PERMISSIONS'):
            broker.handle('domain.config.check',{'managed_hosts':[EXPECTED]})

    def test_no_secret_is_returned_by_preflight(self):
        self.write_runtime()
        result=broker.handle('domain.config.check',{'managed_hosts':[EXPECTED]})
        self.assertTrue(result['ready'])
        self.assertEqual(result['ingress_ipv4'],'56.228.86.160')
        self.assertNotIn('unit-test-fake',repr(result))

    def test_worker_checks_real_hosts_before_dispatch(self):
        root=Path(__file__).resolve().parents[1]
        src=(root/'platform/backend/apps/deployments/runs/worker.py').read_text()
        self.assertIn("'managed_suffix': settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX",src)
        self.assertIn("'managed_hosts': managed_hosts",src)
        self.assertLess(src.index("request_control('domain.config.check'"),src.index('external_id = client.dispatch(run)'))
        self.assertIn('AUTOKOM_DOMAIN_SUFFIX_MISMATCH',src)

if __name__=='__main__': unittest.main()
