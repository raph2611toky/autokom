"""Cloudflare domain cleanup must only delete owned records; offline tests."""
import io
import json
from unittest import TestCase
from unittest.mock import patch

import autokom_platform_broker as broker


class CloudflareResponse:
    def __init__(self, document):
        self.body = io.BytesIO(json.dumps(document).encode('utf-8'))
    def __enter__(self):
        return self.body
    def __exit__(self, *args):
        self.body.close()


class OwnedDNSDeletionTests(TestCase):
    HOST = 'reusable.auto.ikom.mg'
    ADDRESS = '136.116.89.163'
    RECORD_ID = 'a' * 32
    SETTINGS = {
        'zone': 'b' * 32, 'suffix': 'auto.ikom.mg', 'token': 'safe-placeholder',
        'address': ADDRESS, 'zone_name': 'ikom.mg',
    }

    def setUp(self):
        settings = patch.object(broker, '_cloudflare_settings', return_value=self.SETTINGS)
        settings.start()
        self.addCleanup(settings.stop)

    def record(self, *, comment='AUTOKOM managed A record', type='A', address=ADDRESS):
        return {'id': self.RECORD_ID, 'name': self.HOST, 'type': type,
                'content': address, 'proxied': False, 'comment': comment}

    def test_delete_exact_owned_cloudflare_a_record(self):
        requests = []
        def transport(req, timeout):
            requests.append(req)
            return CloudflareResponse({'success': True,
                'result': [self.record()]} if req.get_method() == 'GET'
                else {'success': True})
        with patch.object(broker.urllib.request, 'urlopen', side_effect=transport):
            result = broker._cloudflare_remove_owned_record(self.HOST)
        self.assertTrue(result['deleted'])
        self.assertEqual([r.get_method() for r in requests], ['GET', 'DELETE'])
        self.assertTrue(requests[1].full_url.endswith('/' + self.RECORD_ID))

    def test_record_from_another_owner_must_not_be_deleted(self):
        calls = []
        def transport(req, timeout):
            calls.append(req.get_method())
            return CloudflareResponse({'success': True, 'result': [self.record(comment='manually configured')]})
        with patch.object(broker.urllib.request, 'urlopen', side_effect=transport):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_DOMAIN_DNS_UNOWNED'):
                broker._cloudflare_remove_owned_record(self.HOST)
        self.assertEqual(calls, ['GET'])

    def test_dns_removal_rejects_ambiguous_ipv6_dns(self):
        calls = []
        def transport(req, timeout):
            calls.append(req.get_method())
            return CloudflareResponse({'success': True, 'result': [
                self.record(), self.record(type='AAAA', address='2001:db8::12')]})
        with patch.object(broker.urllib.request, 'urlopen', side_effect=transport):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_DOMAIN_DNS_CONFLICT'):
                broker._cloudflare_remove_owned_record(self.HOST)
        self.assertEqual(calls, ['GET'])

    def test_missing_dns_is_idempotently_clean(self):
        with patch.object(broker.urllib.request, 'urlopen',
                          return_value=CloudflareResponse({'success': True, 'result': []})):
            result = broker._cloudflare_remove_owned_record(self.HOST)
        self.assertEqual(result, {'deleted': False})

    def test_deletion_provider_failure_does_not_report_success(self):
        with patch.object(broker.urllib.request, 'urlopen', side_effect=OSError('failed')):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_CF_DNS_UNAVAILABLE'):
                broker._cloudflare_remove_owned_record(self.HOST)


if __name__ == '__main__':
    import unittest
    unittest.main()
