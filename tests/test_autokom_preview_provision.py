import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
spec = importlib.util.spec_from_file_location('autokom_preview_provision_tests',
                                               ROOT / 'scripts/wireguard/provision.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)
wg = provision.wg


class ProvisionTests(unittest.TestCase):
    def test_first_request_queues_fixed_unit_and_running_job_coalesces(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(wg, 'PROVISION_STATUS', Path(folder) / 'status'):
            for state, starts in [('inactive', True), ('activating', False)]:
                run = MagicMock(return_value=state)
                with self.assertRaisesRegex(wg.WireGuardError, 'PROVISION_PENDING'):
                    wg.provision(run)
                self.assertEqual(any(c.args[0][:3] == ['systemctl', 'start', '--no-block']
                                     for c in run.mock_calls), starts)

    def test_fresh_readiness_validates_running_units_without_reprovision(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(wg, 'PROVISION_STATUS', Path(folder) / 'status'):
            wg.atomic(wg.PROVISION_STATUS, json.dumps({'state':'ready', 'updated':time.time()}))
            run = MagicMock(return_value='')
            wg.provision(run)
            self.assertEqual(run.call_args_list, [
                call(['systemctl','is-active','--quiet',u], timeout=3) for u in provision.UNITS])

    def test_insufficient_cloud_rights_are_not_marked_ready(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(wg, 'PROVISION_STATUS', Path(folder) / 'status'):
            wg.atomic(wg.PROVISION_STATUS, json.dumps({'state':'failed','updated':time.time(),
                                                       'code':'AUTOKOM_PREVIEW_CLOUD_IAM'}))
            with self.assertRaisesRegex(wg.WireGuardError,'CLOUD_IAM'):
                wg.provision(MagicMock(return_value='failed'))

    def test_packages_only_installs_missing_dependencies(self):
        with patch.object(provision.shutil, 'which', side_effect=lambda command: None if command=='dnsmasq' else '/usr/bin/tool'), \
                patch.object(provision, 'plugin_available', return_value=True), \
                patch.object(provision, 'checked') as run:
            provision.packages()
        self.assertEqual(run.call_count, 2)
        self.assertIn('dnsmasq-base', run.call_args.args[0])
        self.assertNotIn('dnsmasq', run.call_args.args[0])
        self.assertNotIn('wireguard-tools', run.call_args.args[0])

    def test_ufw_only_opens_vpn_endpoint_and_tunnel_destinations(self):
        with patch.object(provision.shutil, 'which', return_value='/usr/sbin/ufw'), \
             patch.object(provision, 'checked', return_value='Status: active\n') as run:
            provision.local_firewall()
        self.assertIn(call(['ufw','allow','51896/udp']),run.call_args_list)
        for args, _ in run.call_args_list[2:]:
            self.assertEqual(args[0][2:7], ['in','on','akpreview','to','10.203.53.1'])

    def test_classic_allow_and_policy_deny(self):
        permit = {'direction':'INGRESS','sourceRanges':['0.0.0.0/0'], 'priority':1000,
                  'allowed':[{'IPProtocol':'udp','ports':['51800-51900']}]}
        self.assertTrue(provision.existing_allow({'firewalls':[permit]}))
        self.assertFalse(provision.existing_allow({'firewalls':[permit], 'firewallPolicys':[{'rules':[
            {'direction':'INGRESS','action':'deny','match':{'layer4Configs':[{'ipProtocol':'udp','ports':['51896']}]}}
        ]}]}))

    def test_cloud_rule_targets_only_the_current_instance(self):
        instance={'name':'autokom-primary','id':'12345','tags':{'items':['original'],'fingerprint':'fingerprint'},
                  'networkInterfaces':[{'name':'nic0','accessConfigs':[{'type':'ONE_TO_ONE_NAT','natIP':'34.1.2.3'}],
                  'network':'https://www.googleapis.com/compute/v1/projects/my-project/global/networks/default'}]}
        api=MagicMock()
        effects=[instance, {'firewalls':[]}, None, {'name':'insert-op'}, {'name':'tags-op'},
                 {'firewalls':[{'direction':'INGRESS','sourceRanges':['0.0.0.0/0'],'priority':1000,
                                'allowed':[{'IPProtocol':'udp','ports':['51896']}]}]}]
        api.request.side_effect=effects
        metadata={'project/project-id':'my-project','instance/zone':'projects/123/zones/us-central1-a',
                  'instance/name':'autokom-primary'}
        real_path=provision.Path
        fake=MagicMock()
        fake.is_file.return_value=True
        fake.is_symlink.return_value=False
        fake.stat.return_value.st_uid=0
        fake.stat.return_value.st_mode=0o600
        def path(*values):
            return fake if values[0] in ['/etc/autokom/secrets/runtime.env','/etc/autokom-preprod/runtime.env'] else real_path(*values)
        with patch.object(provision, 'metadata', side_effect=lambda value, **kw: metadata[value]), \
             patch.object(provision, 'Compute', return_value=api), \
             patch.object(provision, 'Path', side_effect=path), \
             patch('autokom_env_manager.read_env', return_value={'AUTOKOM_INGRESS_IPV4':'34.1.2.3'}):
            provision.gcp_firewall()
        rules=[entry.args[2] for entry in api.request.call_args_list if entry.args[:2]==('POST','/global/firewalls')]
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0]['targetTags'],['autokom-preview-12345'])
        self.assertEqual(rules[0]['allowed'],[{'IPProtocol':'udp','ports':['51896']}])
        self.assertIn(call('POST','/zones/us-central1-a/instances/autokom-primary/setTags',
                           {'fingerprint':'fingerprint','items':['original','autokom-preview-12345']}),
                      api.request.call_args_list)
