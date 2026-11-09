"""Regression for AUTOKOM Jenkins agent lifecycle without a real Jenkins server."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autokom_jenkins_agent', ROOT/'scripts/lib/autokom_jenkins_agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

ENV = {'AUTOKOM_JENKINS_URL': 'http://127.0.0.1:8080',
       'AUTOKOM_JENKINS_USER':'AdminJenkins', 'AUTOKOM_JENKINS_API_TOKEN':'redacted'}


def xml_config(*, label='autokom-isolated', launcher='hudson.slaves.JNLPLauncher', executors=2):
    return ('<slave><name>autokom-builder-01</name><remoteFS>/var/lib/autokom-jenkins-agent-v2</remoteFS>'
            '<numExecutors>' + str(executors) + '</numExecutors><label>' + label + '</label><mode>EXCLUSIVE</mode>'
            '<launcher class="' + launcher + '"/></slave>').encode()


class AgentTests(unittest.TestCase):
    def test_loopback_only_avoids_broken_public_websocket(self):
        self.assertEqual(agent.local_url(ENV), 'http://127.0.0.1:8080')
        for url in ('https://jenkins.ik0m.tech', 'http://127.0.0.1:8081',
                    'http://localhost:8080', 'http://127.0.0.1:8080/redirect',
                    'http://admin:pw@127.0.0.1:8080'):
            with self.subTest(url=url), self.assertRaises(agent.AgentError):
                agent.local_url({**ENV, 'AUTOKOM_JENKINS_URL':url})

    def test_existing_node_must_be_isolated(self):
        agent.validate_node(xml_config())
        for data in (xml_config(label='public'), xml_config(launcher='hudson.slaves.CommandLauncher')):
            with self.assertRaises(agent.AgentError):
                agent.validate_node(data)

    def test_create_node_through_approved_rest_not_groovy(self):
        requests=[]
        def fake_http(env, method, path, **kwargs):
            requests.append((method, path, kwargs))
            if method=='GET' and len(requests)==1: return 404, b''
            if method=='POST': return 302, b''
            return 200, xml_config()
        with patch.object(agent,'http',side_effect=fake_http):
            self.assertTrue(agent.ensure_node(ENV))
        self.assertEqual([r[0] for r in requests], ['GET','POST','GET'])
        self.assertIn('/computer/doCreateItem',requests[1][1])
        self.assertIn(b'autokom-builder-01',requests[1][2]['data'])
        self.assertNotIn('scriptText',requests[1][1])

    def test_legacy_executor_count_migrates_only_when_idle(self):
        observed=[]
        def fake_http(env, method, path, **kwargs):
            observed.append((method,path,kwargs))
            if len(observed) == 1: return 200, xml_config(executors=1)
            if len(observed) == 2: return 200, b'{"executors":[{"currentExecutable":null}],"oneOffExecutors":[]}'
            if len(observed) == 3: return 200, b''
            return 200, xml_config(executors=2)
        with patch.object(agent,'http',side_effect=fake_http):
            self.assertTrue(agent.ensure_node(ENV))
        self.assertEqual([c[0] for c in observed], ['GET','GET','POST','GET'])
        self.assertIn('/config.xml', observed[2][1])
        self.assertEqual(observed[2][2]['content_type'],'application/xml')
        self.assertIn(b'<numExecutors>2</numExecutors>',observed[2][2]['data'])

    def test_busy_legacy_executor_is_not_modified(self):
        events=[]
        def fake_http(env, method, path, **kwargs):
            events.append(method)
            if len(events)==1: return 200, xml_config(executors=1)
            return 200, b'{"executors":[{"currentExecutable":{"id":35}}]}'
        with patch.object(agent,'http',side_effect=fake_http):
            with self.assertRaises(agent.AgentError): agent.ensure_node(ENV)
        self.assertEqual(events,['GET','GET'])

    def test_missing_permission_does_not_install_agent(self):
        with patch.object(agent, 'http', return_value=(403,b'')):
            with self.assertRaises(agent.AgentError): agent.ensure_node(ENV)

    def test_secret_from_jnlp_xml_never_printed(self):
        secret = b'a'*64
        doc=b'<jnlp><application-desc><argument>'+secret+b'</argument><argument>node</argument></application-desc></jnlp>'
        with patch.object(agent,'http',return_value=(200,doc)):
            self.assertEqual(agent.get_secret(ENV), secret)
        with patch.object(agent,'http',return_value=(200,b'<html>login</html>')):
            with self.assertRaises(agent.AgentError):agent.get_secret(ENV)

    def test_no_secret_overwrite_without_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            import pwd
            user=pwd.getpwuid(os.getuid())
            target=Path(tmp)/'secret'
            self.assertTrue(agent.write_private_file(target,b'old',user))
            self.assertFalse(agent.write_private_file(target,b'old',user))
            self.assertEqual(target.stat().st_mode & 0o777, 0o400)
            with self.assertRaises(agent.AgentError):agent.write_private_file(target,b'new',user)
            self.assertEqual(target.read_bytes(),b'old')

    def test_symlink_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            import pwd
            u=pwd.getpwuid(os.getuid())
            d=Path(tmp)
            (d/'real').write_bytes(b'secret')
            (d/'link').symlink_to(d/'real')
            with self.assertRaises(agent.AgentError): agent.write_private_file(d/'link',b'new',u)

    def test_unit_runs_local_websocket_no_docker_socket(self):
        content=agent.UNIT_SOURCE.read_text(encoding='utf-8')
        self.assertIn('User=autokom-builder',content)
        self.assertIn('-webSocket',content)
        self.assertIn('-secret @/var/lib/autokom-jenkins-agent-v2/.agent-secret',content)
        self.assertIn('http://127.0.0.1:8080/',content)
        self.assertNotIn('/var/run/docker.sock',content)
        self.assertIn('ProtectSystem=strict',content)

    def test_ansible_provisions_without_docker_membership(self):
        role=(ROOT/'ansible/roles/jenkins_agent/tasks/main.yml').read_text(encoding='utf-8')
        self.assertIn('openjdk-21-jre-headless',role)
        self.assertIn('setfacl',role)
        self.assertIn('autokom-builder',role)
        self.assertIn("'docker' not in autokom_builder_groups.stdout.split()",role)

    def test_cli_agent_entrypoint(self):
        content=(ROOT/'scripts/lib/autokom_runtime_admin.py').read_text(encoding='utf-8')
        self.assertIn("sub.add_parser('agent'",content)
        self.assertIn("return provision(load_env(), apply=opts.apply)",content)

if __name__ == '__main__':unittest.main()

class AgentConnectivityTests(unittest.TestCase):
    def test_new_agent_not_confused_with_legacy_node(self):
        import json
        old={'displayName':'autokom-isolated','offline':True,'temporarilyOffline':False,
             'numExecutors':2,'assignedLabels':[{'name':'autokom-isolated'}]}
        new={'displayName':'autokom-builder-01','offline':False,'temporarilyOffline':False,
             'numExecutors':2,'assignedLabels':[{'name':'autokom-isolated'}]}
        body=json.dumps({'computer':[old,new]}).encode()
        with patch.object(agent,'http',return_value=(200,body)):
            self.assertTrue(agent.exact_agent_online(ENV))
        import autokom_runtime_admin
        with patch.object(autokom_runtime_admin,'jenkins_request',return_value=(200,body)):
            self.assertEqual(autokom_runtime_admin.isolated_agent_status(ENV),(True,'online'))

    def test_agent_must_be_online_not_merely_created(self):
        import json
        node={'displayName':'autokom-builder-01','offline':True,'temporarilyOffline':False,
              'numExecutors':2,'assignedLabels':[{'name':'autokom-isolated'}]}
        with patch.object(agent,'http',return_value=(200,json.dumps({'computer':[node]}).encode())):
            self.assertFalse(agent.exact_agent_online(ENV))
