"""Offline checks for guarded HA DNS promotion and failback."""
import importlib
import ssl
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts/lib'), str(ROOT / 'scripts/ha')]
dns = importlib.import_module('autokom_ha_dns')
broker = importlib.import_module('autokom_platform_broker')
delivery = importlib.import_module('autokom_ha_delivery')
ENV = '11111111-1111-4111-8111-111111111111'
RUN = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
HOST = 'portfoliolady.auto.ikom.mg'
ORIGIN = '136.116.89.163'
ID = 'a' * 32
WORKERS = ['autokom-ha-web-aa11', 'autokom-ha-web-bb22']


class DNSCutover(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name)
        self.override(dns, 'DIRECTORY', folder / 'dns')
        self.override(broker, 'STATE', folder / 'runtime')
        self.override(broker, '_runtime_lock_call', lambda fn, args: fn(args))
        self.override(dns, 'ownership', lambda host: ENV if host == HOST else None)
        self.state = {'run_id': RUN, 'high_availability_requested': True,
                      'cloud_delivery_requested': True, 'healthcheck': '/'}
        self.record = {'type': 'A', 'name': HOST, 'content': ORIGIN, 'proxied': False, 'id': ID, 'ttl': 120}
        self.saved = {}
        self.patches = []
        self.override(dns, 'marker', lambda host: self.saved.get(host))
        self.override(dns, '_write', lambda host, doc: self.saved.update({host: doc}))
        self.override(dns, 'forget', lambda host: self.saved.pop(host, None))
        self.override(dns, '_dns_at', lambda host, address: False)
        self.override(dns, '_version_is_healthy', lambda host, path: path.endswith(RUN))
        self.override(dns, '_record', lambda config, host: [self.record])
        self.override(dns, '_request', self.request)
        self.override(broker, '_load_state', lambda env: self.state)
        self.override(broker, '_cloudflare_settings', lambda: {'address': ORIGIN, 'suffix': 'auto.ikom.mg'})
        self.override(broker, '_customer_https_probe_address', lambda: '127.0.0.1')
        self.override(broker, '_usable_customer_certificate', lambda host: True)
        self.override(broker, '_customer_https_probe_once', lambda host, path, addr: 200)

    def override(self, obj, name, value):
        p = patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def request(self, cfg, path, payload):
        self.assertEqual(path, '/' + ID)
        self.assertEqual(list(payload), ['content'])
        self.assertIn(HOST, self.saved)  # journal survives an ambiguous Cloudflare response
        self.patches.append(payload['content'])
        self.record.update(payload)
        return self.record

    def promote(self):
        return dns.transition(HOST, ENV, RUN, promote=True, workers=WORKERS)

    def test_promote_then_confirm_public_dns(self):
        self.assertEqual(self.promote(), 'DNS_PROPAGATING')
        self.assertEqual(self.patches, [dns.LB_IP])
        self.assertEqual(self.promote(), 'DNS_PROPAGATING')
        self.assertEqual(dns.accepted_ip(HOST, ORIGIN, ENV, ID), dns.LB_IP)
        self.assertIsNone(dns.accepted_ip(HOST, ORIGIN, ENV, 'b' * 32))
        self.override(dns, '_dns_at', lambda host, address: True)
        self.assertEqual(self.promote(), 'PUBLIC_HTTPS_ACTIVE')
        self.assertEqual(self.patches, [dns.LB_IP])

    def test_no_patch_for_unhealthy_release_or_foreign_record(self):
        self.override(dns, '_version_is_healthy', lambda host, path: False)
        self.assertEqual(self.promote(), 'HTTPS_NOT_READY')
        self.record['content'] = '192.0.2.1'
        self.assertEqual(self.promote(), 'DNS_RECORD_CONFLICT')
        self.assertEqual(self.patches, [])

    def test_failback_does_not_drop_marker_before_ttl(self):
        self.promote()
        self.override(dns, '_dns_at', lambda host, address: True)
        self.assertEqual(dns.transition(HOST, ENV, promote=False), 'PRIMARY_PROPAGATING')
        self.assertEqual(self.patches, [dns.LB_IP, ORIGIN])
        self.assertEqual(dns.transition(HOST, ENV, promote=False), 'PRIMARY_PROPAGATING')
        self.saved[HOST]['restored_at'] = int(time.time()) - 130
        self.assertEqual(dns.transition(HOST, ENV, promote=False), 'PRIMARY')
        self.assertNotIn(HOST, self.saved)

    def test_no_tls_bypass(self):
        def failed(*args):
            raise ssl.SSLCertVerificationError('invalid')
        self.override(dns, '_version_is_healthy', failed)
        self.assertEqual(self.promote(), 'HTTPS_PROBE_OR_DNS_UNAVAILABLE')
        self.assertFalse(self.patches)

    def test_retire_restores_a_even_if_primary_is_already_offline(self):
        self.promote()
        self.state = None
        self.assertEqual(dns._change(HOST, ENV, None, False, None, retiring=True), 'PRIMARY')
        self.assertEqual(self.patches, [dns.LB_IP, ORIGIN])

    def test_distinct_expected_endpoints_only(self):
        zones = ['us-central1-a', 'us-central1-f']
        groups = [{'group': 'https://compute.example/zones/' + zone + '/networkEndpointGroups/a'} for zone in zones]
        expected = [{'zone': zone, 'name': name} for zone, name in zip(zones, WORKERS)]
        doc = {'healthStatus': [{'healthState': 'HEALTHY', 'instance': '/instances/' + WORKERS[0]}] * 2}
        with patch.object(delivery, 'compute', return_value=doc):
            self.assertEqual(delivery.backend_healthy({'environment_id': ENV, 'run_id': RUN}, groups, expected=expected), 1)
