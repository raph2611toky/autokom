import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import autokom_ha_delivery as delivery
import autokom_ha_dns as dns
import autokom_platform_broker as broker
import test_autokom_reuse_owned_dns_cleanup_20261110 as cf_cases

ENV = '674be29f-4fe2-4d68-b1c9-554e561b8923'
HOST = 'portfoliolady.auto.ikom.mg'


class ReleaseBrokerTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = root = Path(temporary.name)
        for key in ['STATE', 'PROXY', 'ENABLED']:
            (root / key).mkdir()
            self.use(patch.object(broker, key, root / key))
        self.use(patch.object(broker, 'PREPROD', False))
        self.use(patch.object(broker, '_runtime_dns_env', return_value={'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg'}))
        self.use(patch.object(dns, 'marker', return_value=None))
        self.owner = broker.STATE / ('domain-owner-' + HOST + '.txt')
        self.conf = broker.PROXY / ('autokom-customer-' + HOST + '.conf')
        self.link = broker.ENABLED / self.conf.name
        self.params = {'environment_id': ENV, 'hostnames': [HOST],
                       'release_domains': [{'hostname': HOST, 'managed': True}]}

    def use(self, mock):
        self.addCleanup(mock.stop)
        return mock.start()

    def install(self, owner=ENV):
        self.owner.write_text(owner)
        self.conf.write_text(f'server_name {HOST};')
        self.link.symlink_to(self.conf)

    def test_dns_is_checked_and_deleted_before_vhost_and_retry_is_safe(self):
        self.install()
        states = []
        def cleanup(host, *, dry_run=False, **kwargs):
            states.append((dry_run, self.owner.exists()))
        with patch.object(broker, '_cloudflare_remove_owned_record', side_effect=cleanup) as cf, patch.object(broker, 'run'):
            first = broker._deprovision_environment(self.params)
            self.assertFalse(self.owner.exists())
            second = broker._deprovision_environment(self.params)
        self.assertTrue(first['domains_released'])
        self.assertTrue(second['domains_released'])
        self.assertEqual([call.kwargs['dry_run'] if 'dry_run' in call.kwargs else False for call in cf.call_args_list],
                         [True, False, True, False])
        self.assertEqual(states, [(True, True), (False, True), (True, False), (False, False)])
        self.assertTrue(cf.call_args_list[1].kwargs['legacy_owned_vhost'])
        self.assertFalse(cf.call_args_list[3].kwargs['legacy_owned_vhost'])

    def test_not_allowed_to_remove_dns_of_another_environment(self):
        self.install('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa')
        with patch.object(broker, '_cloudflare_remove_owned_record') as cf:
            with self.assertRaisesRegex(broker.BrokerError, 'OWNERSHIP_MISMATCH'):
                broker._deprovision_environment(self.params)
            cf.assert_not_called()
        self.assertTrue(self.link.is_symlink())

    def test_custom_domain_does_not_touch_dns(self):
        host = 'www.example.org'
        with patch.object(broker, '_cloudflare_remove_owned_record') as cf:
            result = broker._deprovision_environment({'environment_id': ENV, 'hostnames': [host],
                                                        'release_domains': [{'hostname': host, 'managed': False}]})
        self.assertTrue(result['domains_released'])
        cf.assert_not_called()

    def test_conflicting_ha_owner_refused_before_dns(self):
        dns.marker.return_value = {'environment': 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'}
        with patch.object(broker, '_cloudflare_remove_owned_record') as cf:
            with self.assertRaisesRegex(broker.BrokerError, 'OWNERSHIP_MISMATCH'):
                broker._deprovision_environment(self.params)
            cf.assert_not_called()

    def test_failed_gcp_route_removal_does_not_confirm_release(self):
        unit = self.root / 'autokom-ha-delivery.service'
        unit.touch()
        def check_lock(_):
            self.assertTrue(delivery.LOCK.exists())
            raise delivery.DeliveryError('offline')
        with patch.object(os, 'fstat', wraps=os.fstat) as fileinfo, patch.object(broker, 'DELIVERY_UNIT', unit), \
                patch.object(delivery, 'LOCK', self.root / 'delivery.lock'), \
                patch.object(delivery, 'unroute_deleted', side_effect=check_lock) as unroute, \
                patch.object(broker, '_deprovision_environment', return_value={'domains_released':True}):
            if os.getuid() != 0:
                from types import SimpleNamespace
                from stat import S_IFREG
                fileinfo.return_value = SimpleNamespace(st_mode=S_IFREG | 0o600, st_uid=0)
                fileinfo.side_effect = lambda _: fileinfo.return_value
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HA_ROUTE_REMOVE_FAILED'):
                broker._retire_runtime(self.params)
            unroute.assert_called_once_with([ENV])


class HARetiredRecordTests(TestCase):
    def test_ha_record_requires_exact_persisted_identity(self):
        cfg = cf_cases.OwnedDNSDeletionTests.SETTINGS
        record = {'type': 'A', 'name': HOST, 'id': 'a' * 32, 'content': '35.186.239.102', 'proxied': False}
        marker = {'id': 'b' * 32, 'origin': cfg['address'], 'ip': record['content']}
        with patch.object(broker, '_cloudflare_settings', return_value=cfg), \
                patch.object(broker.urllib.request, 'urlopen',
                             side_effect=lambda *a, **k: cf_cases.CloudflareResponse({'success': True, 'result': [record]})):
            with self.assertRaisesRegex(broker.BrokerError, 'DNS_CONFLICT'):
                broker._cloudflare_remove_owned_record(HOST, ha_owner=marker, dry_run=True)
            marker['id'] = record['id']
            self.assertEqual(broker._cloudflare_remove_owned_record(HOST, ha_owner=marker, dry_run=True),
                             {'deletable': True})
