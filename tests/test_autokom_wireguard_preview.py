import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts' / part) for part in ('lib', 'ha')]
import autokom_platform_broker as broker
import autokom_preview_access as access
import autokom_wireguard as wg
import autokom_ha_delivery as cloud

ENV = '5a7d0000-e935-44ab-8d12-001200004001'
OTHER = '5a7d0001-e935-44ab-8d12-001200004002'
HOST = 'pre.demo.auto.ikom.mg'
PEER = '10.203.1.20'
PUB = base64.b64encode(bytes([11]) * 32).decode()
PRIV = base64.b64encode(bytes([22]) * 32).decode()
SERVER_KEY = base64.b64encode(bytes([33]) * 32).decode()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.setup = ExitStack()
        self.addCleanup(self.setup.close)
        root = Path(self.setup.enter_context(tempfile.TemporaryDirectory()))
        self.execution = self.setup.enter_context(patch.object(broker, 'run', return_value=''))
        self.setup.enter_context(patch.object(wg, 'provision'))
        self.public_record = self.setup.enter_context(patch.object(broker, '_cloudflare_record'))
        self.public_dns = self.setup.enter_context(patch.object(broker, '_check_dns'))
        self.verify_public = self.setup.enter_context(patch.object(broker, '_verify_customer_https'))
        self.verify_private = self.setup.enter_context(patch.object(access, 'verify_private_https'))
        for module, fields in [
            (broker, {'STATE': root / 'state', 'PROXY': root / 'available', 'ENABLED': root / 'enabled',
                      'PREPROD': False, 'DELIVERY_UNIT': root / 'delivery-unit', 'DELIVERY_STATE': root / 'delivery'}),
            (access, {'HOSTS': root / 'split-dns'}),
            (wg, {'ROOT': root / 'vpn', 'REGISTRY': root / 'vpn/peers.json', 'SYNC': root / 'vpn/peers.conf'}),
        ]:
            self.setup.enter_context(patch.multiple(module, **fields))
        self.setup.enter_context(patch.object(broker, '_runtime_dns_env', return_value={
            'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg', 'AUTOKOM_INGRESS_IPV4': '8.8.8.8',
            'AUTOKOM_PREPROD_ALLOWED_HOSTS': HOST,
        }))
        self.setup.enter_context(patch.object(broker, 'https_listens', return_value='    listen 443 ssl;\n'))
        for directory in (broker.STATE / ENV, broker.PROXY, broker.ENABLED, access.HOSTS, wg.ROOT):
            directory.mkdir(parents=True, mode=0o700)
        wg.atomic(wg.ROOT / 'server.pub', SERVER_KEY + '\n')
        wg.atomic(wg.ROOT / 'akpreview.conf', '[Interface]\nPrivateKey = ' + SERVER_KEY + '\n')
        self.params = {'kind': 'STAGING', 'environment_id': ENV, 'hostnames': [HOST]}
        self.path = access.policy_path(broker.STATE, ENV)
        self.config, self.link, self.owner = access.paths(broker, HOST)
        self.public = broker._nginx_config(HOST, 20901, tls=True)

    def live(self):
        broker._save_state(ENV, {'environment_id': ENV, 'port': 20901, 'healthcheck': '/health'})
        self.config.write_text(self.public)
        self.link.symlink_to(self.config)
        self.owner.write_text(ENV)

    def private(self, value=True):
        return broker.handle('preview.visibility', {**self.params, 'private': value})

    def credentials(self, operation):
        return broker.handle('preview.credentials', {**self.params, 'operation': operation})

    def test_default_and_production_remain_public(self):
        self.live()
        status = broker.handle('preview.status', self.params)
        self.assertEqual(status, {'private': False, 'issued': False})
        with self.assertRaisesRegex(broker.BrokerError, 'STAGING_ONLY'):
            broker.handle('preview.visibility', {**self.params, 'kind': 'PRODUCTION', 'private': True})
        self.assertEqual(self.config.read_text(), self.public)
        self.execution.assert_not_called()

    def test_private_before_first_release_never_creates_public_vhost(self):
        self.assertEqual(self.private(), {'private': True})
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.config.exists())
        with self.assertRaisesRegex(broker.BrokerError, 'NOT_DEPLOYED'):
            self.credentials('issue')

    def test_existing_release_can_be_hidden_then_made_public(self):
        self.live()
        self.private()
        document = self.config.read_text()
        self.assertTrue(document.startswith(access.MARKER))
        self.assertEqual(access.dns_path(HOST).read_text(), f'{wg.SERVER} {HOST}\n')
        self.verify_private.assert_called_once_with(broker, HOST, '/health')
        self.assertIn('$server_addr:$realip_remote_addr', document)
        self.assertNotIn('proxy_pass', document.split('listen 0.0.0.0:18443')[0])
        self.assertEqual(document.count('proxy_pass'), 1)
        self.assertEqual(self.private(False), {'private': False})
        self.assertEqual(self.config.read_text(), self.public)
        self.assertFalse(access.dns_path(HOST).exists())
        self.assertFalse(self.path.exists())
        self.verify_public.assert_called_once_with(HOST, '/health')

    def test_failed_first_cutover_restores_original_site(self):
        self.live()
        self.verify_private.side_effect = broker.BrokerError('private probe failed')
        with self.assertRaisesRegex(broker.BrokerError, 'private probe failed'):
            self.private()
        self.assertFalse(self.path.exists())
        self.assertFalse(access.dns_path(HOST).exists())
        self.assertEqual(self.config.read_text(), self.public)

    def test_failed_return_to_public_preserves_private_route(self):
        self.live()
        self.private()
        original = self.config.read_text()
        self.verify_public.side_effect = broker.BrokerError('public probe failed')
        with self.assertRaisesRegex(broker.BrokerError, 'public probe failed'):
            self.private(False)
        self.assertTrue(self.path.is_file())
        self.assertTrue(access.dns_path(HOST).is_file())
        self.assertEqual(self.config.read_text(), original)

    def test_other_environment_cannot_claim_the_same_vhost(self):
        self.live()
        with self.assertRaisesRegex(broker.BrokerError, 'OWNERSHIP_MISMATCH'):
            broker.handle('preview.visibility', {**self.params, 'environment_id': OTHER, 'private': True})
        self.assertEqual(self.config.read_text(), self.public)

    def test_issue_rotation_and_revoke_do_not_persist_client_private_key(self):
        self.live()
        self.private()
        keys = iter([PUB, base64.b64encode(bytes([44]) * 32).decode()])
        def execute(argv, **kwargs):
            if argv == ['wg', 'genkey']:
                return PRIV
            if argv == ['wg', 'pubkey']:
                self.assertEqual(kwargs['input'], PRIV + '\n')
                return next(keys)
            return ''
        self.execution.side_effect = execute
        self.assertIn('PrivateKey = ' + PRIV, self.credentials('issue')['config'])
        self.assertIn('AllowedIPs = ' + wg.SERVER + '/32', self.credentials('issue')['config'])
        saved = wg.read(wg.SYNC)
        self.assertNotIn(PUB, saved)
        self.assertNotIn(PRIV, saved + wg.read(wg.REGISTRY))
        self.assertIn('PrivateKey = ' + SERVER_KEY, saved)
        self.assertEqual(self.credentials('revoke'), {'issued': False})
        self.assertNotIn('[Peer]', wg.read(wg.SYNC))
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.credentials('status'), {'issued': False})

    def test_normal_domain_republication_keeps_private_policy(self):
        self.live()
        self.private()
        with patch.object(broker, '_usable_customer_certificate', return_value=True):
            result = broker._proxy_apply(HOST, ENV, managed=True)
        self.assertTrue(result['active'])
        self.assertTrue(self.config.read_text().startswith(access.MARKER))
        self.assertEqual(self.verify_private.call_count, 2)

    def test_private_activation_uses_dns01_and_not_public_a(self):
        self.live()
        self.private()
        with patch.object(broker, '_usable_customer_certificate', return_value=False), \
             patch.object(broker, '_issue_customer_certificate') as cert:
            self.assertTrue(broker.handle('domain.activate', {**self.params, 'hostname': HOST,
                        'managed': True, 'wireguard_only': True})['active'])
        cert.assert_called_once_with(HOST, managed=True)
        self.public_record.assert_not_called()
        self.public_dns.assert_not_called()
        self.assertTrue(self.config.read_text().startswith(access.MARKER))

    def test_private_intent_with_missing_policy_cannot_publish_public(self):
        self.live()
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_PREVIEW_STATE_INVALID'):
            broker.handle('domain.activate', {**self.params, 'hostname': HOST,
                          'managed': True, 'wireguard_only': True})
        self.public_dns.assert_not_called()
        self.assertEqual(self.config.read_text(), self.public)

    def test_public_reactivation_waits_for_dns_without_unmasking(self):
        self.live()
        self.private()
        before = self.config.read_text()
        self.public_dns.side_effect = broker.BrokerError('AUTOKOM_DNS_A_MISMATCH: not ready')
        with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_PREVIEW_PUBLIC_DNS_PENDING'):
            self.private(False)
        self.assertEqual(self.config.read_text(), before)
        self.assertTrue(self.path.is_file())

    def test_new_preview_does_not_need_the_cloud_delivery_lock(self):
        with patch.object(broker, 'delivery_configured', return_value=True), \
             patch.object(broker, 'delivery_module') as controller:
            self.assertEqual(self.private(), {'private': True})
        controller.assert_not_called()

    def test_rollback_does_not_restore_a_public_proxy(self):
        self.live()
        self.private()
        current = {'environment_id': ENV, 'run_id': OTHER, 'port': 20902, 'name': 'new'}
        previous = {'environment_id': ENV, 'run_id': ENV, 'port': 20901, 'name': 'old'}
        broker._save_state(ENV, current)
        wg.atomic(broker.STATE / ENV / 'previous.json', json.dumps(previous))
        self.assertTrue(broker._rollback({'environment_id': ENV, 'run_id': OTHER})['rolled_back'])
        self.assertTrue(self.config.read_text().startswith(access.MARKER))
        self.assertIn('127.0.0.1:20901', self.config.read_text())

    def test_public_https_not_confirmed_if_wan_still_serves_app(self):
        self.setup.close()
        with patch.object(access, 'verify_split_dns'), \
             patch.object(broker, '_verify_customer_https'), \
             patch.object(broker, '_customer_https_probe_once', return_value=200), \
             patch.object(access.time, 'sleep'):
            with self.assertRaisesRegex(broker.BrokerError, 'PUBLIC_ROUTE'):
                access.verify_private_https(broker, HOST, '/')


class CloudPreviewTests(unittest.TestCase):
    def test_only_target_hostname_is_fenced_in_shared_map(self):
        matcher = 'akp-' + ENV.replace('-', '')[:20]
        other = {'pathMatcher': 'akp-other', 'hosts': ['public.auto.ikom.mg']}
        doc = {'fingerprint': 'old', 'hostRules': [other, {'hosts': [HOST], 'pathMatcher': matcher}],
               'pathMatchers': [{'name': 'akp-other', 'defaultService': 'other'},
                                {'name': matcher, 'defaultService': 'old'}]}
        patch_body = {}
        def compute(method, route, payload=None, **kwargs):
            if method == 'PATCH':
                patch_body.update(payload)
                return {'name': 'test'}
            return patch_body or doc
        with patch.object(cloud, 'compute', side_effect=compute), patch.object(cloud, 'operation'):
            self.assertTrue(cloud.isolate_preview(ENV, [HOST]))
        self.assertIn(other, patch_body['hostRules'])
        self.assertEqual(patch_body['pathMatchers'][-1]['defaultUrlRedirect']['hostRedirect'],
                         'preview-unavailable.invalid')

    def test_private_app_skipped_even_if_previous_image_is_cloud_eligible(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cloud, 'STATE', Path(temp)):
            path = Path(temp) / ENV
            path.mkdir()
            (path / 'preview-access.json').touch()
            with patch.object(cloud, '_private_json') as read:
                self.assertEqual(cloud.committed_apps(), [])
                read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
