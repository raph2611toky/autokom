"""Regression of fail-closed project/domain teardown without Docker or nginx mutations."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import autokom_platform_broker as broker

ENV = '674be29f-4fe2-4d68-b1c9-554e561b8923'
HOST = 'portfolio.auto.ikom.mg'


class ProjectTeardownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ('state', 'available', 'enabled'):
            (root/name).mkdir()
        self.state = root/'state'
        self.available = root/'available'
        self.enabled = root/'enabled'
        self.patches = [patch.object(broker, 'STATE', self.state),
                        patch.object(broker, 'PROXY', self.available),
                        patch.object(broker, 'ENABLED', self.enabled)]
        for obj in self.patches:
            obj.start()
            self.addCleanup(obj.stop)

    def layout(self):
        conf = self.available/('autokom-customer-'+HOST+'.conf')
        conf.write_text(f'server {{\n server_name {HOST};\n}}\n')
        link = self.enabled/conf.name
        link.symlink_to(conf)
        return conf,link

    def test_unowned_file_is_not_deleted(self):
        conf, link = self.layout()
        with self.assertRaises(broker.BrokerError):
            broker._deactivate_owned_vhost(HOST, ENV)
        self.assertTrue(conf.exists())
        self.assertTrue(link.is_symlink())

    def test_other_environment_cannot_delete_vhost(self):
        conf, link = self.layout()
        marker = self.state/('domain-owner-'+HOST+'.txt')
        marker.write_text('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa')
        with self.assertRaises(broker.BrokerError):
            broker._deactivate_owned_vhost(HOST, ENV)
        self.assertTrue(conf.exists())
        self.assertTrue(link.is_symlink())

    def test_owned_vhost_removed_and_nginx_reloaded(self):
        conf, link = self.layout()
        marker = self.state/('domain-owner-'+HOST+'.txt')
        marker.write_text(ENV)
        with patch.object(broker, 'run') as execute:
            self.assertTrue(broker._deactivate_owned_vhost(HOST, ENV))
        self.assertEqual(execute.call_count, 2)
        self.assertFalse(conf.exists())
        self.assertFalse(link.is_symlink())
        self.assertFalse(marker.exists())
        with patch.object(broker, 'run'):
            self.assertFalse(broker._deactivate_owned_vhost(HOST,ENV))

    def test_nginx_reload_failure_restores_exact_vhost(self):
        conf, link = self.layout()
        original = conf.read_bytes()
        marker = self.state/('domain-owner-'+HOST+'.txt')
        marker.write_text(ENV)
        attempts = [None, broker.BrokerError('reload failed'), None, None]
        with patch.object(broker, 'run', side_effect=attempts):
            with self.assertRaises(broker.BrokerError):
                broker._deactivate_owned_vhost(HOST, ENV)
        self.assertEqual(conf.read_bytes(), original)
        self.assertTrue(link.is_symlink())
        self.assertTrue(marker.exists())

    def test_deprovision_is_idempotent_when_no_runtime_exists(self):
        with patch.object(broker,'run') as call:
            result = broker._deprovision_environment({'environment_id':ENV,'hostnames':[]})
        self.assertTrue(result['offline'])
        call.assert_not_called()

    def test_teardown_rejects_invalid_or_duplicate_hosts(self):
        for hosts in ([HOST,HOST], ['../../etc/passwd'], 'any'):
            with self.assertRaises(broker.BrokerError):
                broker._deprovision_environment({'environment_id':ENV,'hostnames':hosts})

if __name__ == '__main__':
    unittest.main()
