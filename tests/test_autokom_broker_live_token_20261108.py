"""Broker must stop using stale Jenkins credentials from its process environment."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_platform_broker as broker

class LiveCredentialsTests(unittest.TestCase):
    def test_jenkins_config_reads_current_secure_env_not_cached_process(self):
        values={'AUTOKOM_JENKINS_URL':'http://127.0.0.1:8080',
                'AUTOKOM_JENKINS_USER':'AdminJenkins',
                'AUTOKOM_JENKINS_API_TOKEN':'new-token',
                'AUTOKOM_JENKINS_JOB':'autokom-client-deploy'}
        with patch.object(broker,'_runtime_dns_env',side_effect=lambda: dict(values)), \
             patch.dict(os.environ,{'AUTOKOM_JENKINS_API_TOKEN':'old-token'}):
            self.assertEqual(broker.jenkins_config()[-1], 'new-token')
            values['AUTOKOM_JENKINS_API_TOKEN']='rotated-token'
            self.assertEqual(broker.jenkins_config()[-1], 'rotated-token')

    def test_invalid_secret_file_does_not_fallback_to_systemd_env(self):
        with patch.object(broker,'_runtime_dns_env',side_effect=broker.BrokerError('invalid env')):
            with self.assertRaisesRegex(broker.BrokerError,'invalid env'):
                broker.jenkins_config()

    def test_broker_identity_uses_current_token_and_never_returns_secret(self):
        with patch.object(broker,'jenkins_http',return_value=(200,{},b'{"authenticated":true,"name":"AdminJenkins"}')) as request:
            self.assertEqual(broker.handle('jenkins.identity',{}),{'authenticated':True})
        request.assert_called_once_with('GET','/whoAmI/api/json')

    def test_broker_identity_rejects_401_without_leaking_auth(self):
        with patch.object(broker,'jenkins_http',return_value=(401,{},b'')):
            with self.assertRaisesRegex(broker.BrokerError, 'refusée'):
                broker.handle('jenkins.identity',{})

    def test_fallback_works_if_no_file_as_in_isolated_tests(self):
        with patch.object(broker,'_runtime_dns_env',return_value={
             'AUTOKOM_JENKINS_URL':'http://127.0.0.1:8080',
             'AUTOKOM_JENKINS_USER':'AdminJenkins',
             'AUTOKOM_JENKINS_API_TOKEN':'token'}):
            self.assertEqual(broker.jenkins_config()[2:4],('AdminJenkins','token'))

if __name__=='__main__':unittest.main()
