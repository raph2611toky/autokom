import importlib.util
import pathlib
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts/ha/autokom_ha_preflight.py'
spec = importlib.util.spec_from_file_location('autokom_ha_preflight', SCRIPT)
ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ha)

class TestInventory(unittest.TestCase):
    def test_health_nested(self):
        report = {'healthStatus': [{'healthState': 'HEALTHY'}, {'healthState': 'UNHEALTHY'}]}
        self.assertEqual(ha.find_healths([report]), ['HEALTHY', 'UNHEALTHY'])

    def test_validation_bootstrap_identified(self):
        template = {'properties': {'metadata': {'items': [
            {'key': 'startup-script', 'value': 'cat >/somewhere/validation_server.py'}]}}}
        self.assertEqual(ha.bootstrap_mode(template), 'validation')

    def test_prometheus_is_read_only_and_safe(self):
        report = {'instance_count': 2, 'healthy_backends': 2,
                  'issues': ['not ready'], 'can_host_customer_applications': False,
                  'lb_requests_5m': {'5xx': 3}}
        result = ha.to_prometheus(report)
        self.assertIn('autokom_ha_lb_requests_5m{response_class="5xx"} 3', result)
        self.assertIn('autokom_ha_readiness_ok 0', result)

    @patch.object(ha, 'command')
    def test_validation_mig_blocks_customer_ready(self, cmd):
        def reply(*args):
            action = ' '.join(args)
            if ' managed describe ' in ' ' + action + ' ':
                return {'versions': [{'instanceTemplate': '.../autokom-ha-tpl-test'}]}
            if ' managed list-instances ' in ' ' + action + ' ':
                return [{'instance': '.../zones/us-central1-a/instances/web-a', 'instanceStatus': 'RUNNING'},
                        {'instance': '.../zones/us-central1-f/instances/web-b', 'instanceStatus': 'RUNNING'}]
            if ' backend-services describe ' in ' ' + action + ' ':
                return {'portName': 'http'}
            if ' backend-services get-health ' in ' ' + action + ' ':
                return {'healthStatus': [{'healthState': 'HEALTHY'}, {'healthState': 'HEALTHY'}]}
            if ' url-maps describe ' in ' ' + action + ' ':
                return {'defaultService': '...'}
            if ' forwarding-rules describe ' in ' ' + action + ' ':
                return {'IPAddress': '35.186.239.102'}
            if ' instance-templates describe ' in ' ' + action + ' ':
                return {'properties': {'metadata': {'items': [{'key': 'startup-script', 'value': 'validation_server.py'}]}}}
            raise AssertionError(action)
        cmd.side_effect = reply
        res = ha.inventory('plucky-stacker-480421-c4', 'us-central1', 'autokom-ha-rmig', 'autokom-ha-backend', 'autokom-ha-map', 'autokom-ha-http', False)
        self.assertEqual(res['healthy_backends'], 2)
        self.assertEqual(res['instance_count'], 2)
        self.assertEqual(res['bootstrap_mode'], 'validation')
        self.assertFalse(res['can_host_customer_applications'])
        self.assertTrue(any('VALIDATION' in item for item in res['issues']))

if __name__ == '__main__':
    unittest.main()
