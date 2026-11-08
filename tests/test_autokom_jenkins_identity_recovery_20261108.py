"""No network/systemd mutations: Jenkins 401 is not a dead controller."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/lib'))
import autokom_jenkins_agent as agent

ENV={'AUTOKOM_JENKINS_URL':'http://127.0.0.1:8080',
     'AUTOKOM_JENKINS_USER':'AdminJenkins', 'AUTOKOM_JENKINS_API_TOKEN':'test-token'}

class JenkinsIdentityTests(unittest.TestCase):
    def test_success_is_authenticated_api_not_login_html(self):
        with patch.object(agent,'http',return_value=(200, b'{"authenticated":true,"name":"AdminJenkins"}')) as request:
            self.assertTrue(agent.verify_jenkins_identity(ENV))
        self.assertEqual(request.call_args.args[2],'/whoAmI/api/json')

    def test_401_is_credential_failure_not_service_down(self):
        with patch.object(agent,'http',return_value=(401,b'')):
            with self.assertRaisesRegex(agent.AgentError,'token refusé'):
                agent.verify_jenkins_identity(ENV)

    def test_403_is_credential_failure(self):
        with patch.object(agent,'http',return_value=(403,b'')):
            with self.assertRaisesRegex(agent.AgentError,'HTTP 403'):
                agent.verify_jenkins_identity(ENV)

    def test_200_login_html_is_not_accepted_as_identity(self):
        with patch.object(agent,'http',return_value=(200,b'<html>login</html>')):
            with self.assertRaisesRegex(agent.AgentError,'identité invalide'):
                agent.verify_jenkins_identity(ENV)

    def test_200_anonymous_is_not_accepted(self):
        with patch.object(agent,'http',return_value=(200,b'{"authenticated":false}')):
            with self.assertRaisesRegex(agent.AgentError,'session anonyme'):
                agent.verify_jenkins_identity(ENV)

    def test_200_non_dict_is_not_accepted(self):
        with patch.object(agent,'http',return_value=(200,b'[]')):
            with self.assertRaisesRegex(agent.AgentError,'session anonyme'):
                agent.verify_jenkins_identity(ENV)

    def test_302_redirect_is_refused(self):
        with patch.object(agent,'http',return_value=(302,b'')):
            with self.assertRaisesRegex(agent.AgentError,'HTTP 302'):
                agent.verify_jenkins_identity(ENV)

    def test_apply_fails_before_ansible_if_credentials_rejected(self):
        with patch.object(agent,'backend_url',return_value='https://backend.auto.ikom.mg'), \
             patch.object(agent,'http',return_value=(401,b'')), \
             patch.object(agent,'provision_system_dependencies',side_effect=AssertionError('do not mutate host')):
            with self.assertRaisesRegex(agent.AgentError,'token refusé'):
                agent.provision(ENV,apply=True)

    def test_check_also_validates_real_jenkins_auth(self):
        with patch.object(agent,'backend_url',return_value='https://backend.auto.ikom.mg'), \
             patch.object(agent,'http',return_value=(401,b'')):
            with self.assertRaisesRegex(agent.AgentError,'token refusé'):
                agent.provision(ENV,apply=False)

if __name__=='__main__': unittest.main()
