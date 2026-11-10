"""Read-only authoritative availability checks without network requests."""
import io
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import autokom_platform_broker as broker


class FakeResponse:
    def __init__(self, records):
        self.stream = io.BytesIO(json.dumps({'success': True, 'result': records}).encode())
    def __enter__(self):
        return self.stream
    def __exit__(self, exc_type, exc, tb):
        self.stream.close()


class AvailabilityBrokerTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.patches = [
            patch.object(broker, 'STATE', base / 'state'),
            patch.object(broker, 'PROXY', base / 'nginx'),
            patch.object(broker, 'ENABLED', base / 'enabled'),
            patch.object(broker, '_cloudflare_settings', return_value={
                'suffix': 'auto.ikom.mg', 'zone': 'a' * 32,
                'token': 'dummy-test-secret', 'zone_name': 'ikom.mg', 'address': '136.116.89.163'}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        for directory in (broker.STATE, broker.PROXY, broker.ENABLED):
            directory.mkdir()

    def test_unused_managed_host_is_available(self):
        with patch.object(broker.urllib.request, 'urlopen', return_value=FakeResponse([])) as urlopen:
            result = broker.handle('domain.available', {'hostname': 'free.auto.ikom.mg'})
        self.assertTrue(result['available'])
        self.assertIn('dns_records', urlopen.call_args[0][0].full_url)

    def test_existing_cloudflare_record_is_never_reused_even_if_ip_matches(self):
        record = {'name': 'taken.auto.ikom.mg', 'type': 'A', 'content': '136.116.89.163',
                  'comment': 'not autokom'}
        with patch.object(broker.urllib.request, 'urlopen', return_value=FakeResponse([record])):
            result = broker.handle('domain.available', {'hostname': 'taken.auto.ikom.mg'})
        self.assertFalse(result['available'])

    def test_runtime_owner_marker_blocks_availability_without_network(self):
        (broker.STATE / 'domain-owner-used.auto.ikom.mg.txt').write_text('environment-1')
        with patch.object(broker.urllib.request, 'urlopen') as urlopen:
            result = broker.handle('domain.available', {'hostname': 'used.auto.ikom.mg'})
        self.assertFalse(result['available'])
        urlopen.assert_not_called()

    def test_reserved_backend_prefix_rejected(self):
        with patch.object(broker.urllib.request, 'urlopen') as urlopen:
            result = broker.handle('domain.available', {'hostname': 'backend.auto.ikom.mg'})
        self.assertFalse(result['available'])
        urlopen.assert_not_called()

    def test_cloudflare_failure_is_not_declared_available(self):
        with patch.object(broker.urllib.request, 'urlopen', side_effect=OSError('network error')):
            with self.assertRaises(broker.BrokerError):
                broker.handle('domain.available', {'hostname': 'free.auto.ikom.mg'})
