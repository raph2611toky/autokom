"""AUTOKOM runtime regression: isolation, attach readiness, and recipe UID.

All checks are hermetic: no Docker daemon, Jenkins or real network required.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'/'lib'))
sys.path.insert(0,str(ROOT/'scripts'/'runtime'))
import autokom_internal_ingress as ingress
from autokom_backend_recipes import backend_recipe, BACKEND_RUNTIME
from autokom_frontend_recipes import recipe as frontend_recipe
import autokom_customer_diagnostics as diagnostics

NAME = 'autokom-' + 'a'*20
NET = 'autokom-' + 'b'*20
STACK = 'autokom-stack-' + 'c'*20


def mock_docker(network, attachments):
    calls=[]
    iterator=iter(attachments)
    def command(argv, **kwargs):
        calls.append(argv)
        if argv[:3]==['docker','network','inspect']:
            return json.dumps([{'Name':network,'Id':'net123','Internal':True,
                                'Driver':'bridge','IPAM':{'Config':[{'Subnet':'172.24.0.0/16'}]}}])
        if argv[:3]==['docker','container','inspect']:
            return json.dumps(next(iterator))
        raise AssertionError(argv)
    return command,calls


def attached(network, *, ip='172.24.0.2', identity='net123'):
    return {network:{'IPAddress':ip,'NetworkID':identity}}


class RuntimeNetworkReadinessTests(unittest.TestCase):
    def test_stack_frontend_and_backend_share_valid_private_network(self):
        command,_=mock_docker(STACK,[attached(STACK)])
        self.assertEqual(ingress.container_ipv4(NAME,STACK,command),'172.24.0.2')

    def test_single_backend_network_also_supported(self):
        command,_=mock_docker(NET,[attached(NET)])
        self.assertEqual(ingress.container_ipv4(NAME,NET,command),'172.24.0.2')

    def test_ipam_delayed_then_ready_is_retried(self):
        command,calls=mock_docker(NET,[{},{NET:{'IPAddress':'','NetworkID':'net123'}},attached(NET)])
        with patch.object(ingress.time,'sleep') as sleeper:
            self.assertEqual(ingress.container_ipv4(NAME,NET,command),'172.24.0.2')
        self.assertEqual(sleeper.call_count,2)
        self.assertEqual(sum(c[:3]==['docker','container','inspect'] for c in calls),3)

    def test_unsafe_side_network_is_rejected_without_retry(self):
        command,calls=mock_docker(NET,[{**attached(NET),'bridge':{'IPAddress':'172.17.0.2'}}])
        with patch.object(ingress.time,'sleep') as sleeper:
            with self.assertRaises(ingress.IngressError) as context:
                ingress.container_ipv4(NAME,NET,command)
        self.assertEqual(context.exception.reason,'unexpected_networks')
        sleeper.assert_not_called()
        self.assertEqual(sum(c[:3]==['docker','container','inspect'] for c in calls),1)

    def test_outside_subnet_or_wrong_network_identity_still_refused(self):
        for candidate, reason in [(attached(NET,ip='172.25.0.2'),'ipv4_outside_subnet'),
                                  (attached(NET,identity='other'),'network_id_mismatch')]:
            command,_=mock_docker(NET,[candidate])
            with self.subTest(reason=reason),self.assertRaises(ingress.IngressError) as context:
                ingress.container_ipv4(NAME,NET,command)
            self.assertEqual(context.exception.reason,reason)

    def test_network_name_cannot_be_arbitrary(self):
        with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_NETWORK_INVALID_NAME'):
            ingress.container_ipv4(NAME,'public',lambda *_a,**_k:None)


class SafeDiagnosticsTests(unittest.TestCase):
    def test_safe_network_reason_is_in_postmortem(self):
        env='a'*8+'-aaaa-4aaa-8aaa-'+'a'*12
        rid='b'*8+'-bbbb-4bbb-8bbb-'+'b'*12
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(diagnostics, '_read_docker', side_effect=['{}','']):
            record=diagnostics.capture(directory,env,rid,NAME,
                                       reason='network_id_mismatch')
            self.assertEqual(record['reason'],'network_id_mismatch')
            self.assertEqual(diagnostics.read(directory,env,rid)['reason'],
                             'network_id_mismatch')

    def test_reason_and_nginx_errors_are_bounded(self):
        self.assertIsNone(diagnostics.safe_log_line('SECRET=topsecret'))
        self.assertIsNone(diagnostics.safe_log_line('nginx: [emerg] open() "/etc/shadow" failed (13: Permission denied)'))
        safe='nginx: [emerg] mkdir() "/var/cache/nginx/client_temp" failed (13: Permission denied)'
        self.assertEqual(diagnostics.safe_log_line(safe),safe)


class BuildRecipeRuntimeUserTests(unittest.TestCase):
    def test_frontend_uses_runtime_uid_static_and_next(self):
        for profile in ('vite','html','next_server'):
            cfg={'output_directory':'dist' if profile=='vite' else '',
                 'build_command':'npm run build' if profile!='html' else '',
                 'start_command':'npm run start' if profile=='next_server' else ''}
            dockerfile,_=frontend_recipe(profile,cfg,{'mode':'DEVSECOPS'})
            self.assertIn('USER 10001:10001',dockerfile)
            self.assertNotIn('USER node',dockerfile)

    def test_backend_node_and_php_use_runtime_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)
            (path/'package.json').write_text(json.dumps({'dependencies':{'express':'*'},
                                                   'scripts':{'start':'node index.js'}}))
            (path/'package-lock.json').write_text('{}')
            dockerfile,_=backend_recipe('node_express',path,{'port':8000,'runtime':'NODE',
                                 'build_command':'','start_command':'','output_directory':'',
                                 'autokom_mode':'DEVOPS'})
            self.assertIn('COPY --chown=10001:10001',dockerfile)
            self.assertIn('USER 10001:10001',dockerfile)
            (path/'index.php').write_text('<?php echo "ok";')
            php,_=backend_recipe('php_plain',path,{'port':8000,'runtime':'PHP',
                                'build_command':'','start_command':'','output_directory':''})
            self.assertIn('chown -R 10001:10001',php)
            self.assertIn('USER 10001:10001',php)

    def test_vite_build_legacy_path_runtime_uid(self):
        agent=(ROOT/'scripts/runtime/autokom_agent_local.py').read_text()
        self.assertIn("'USER 10001:10001\\nEXPOSE 8080\\n'",agent)


if __name__=='__main__':unittest.main()
