"""Hermetic ingress security tests: no Docker, network, Nginx or root required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import autokom_internal_ingress as ingress


NAME = 'autokom-'+'c'*20
NET = 'autokom-'+'e'*20
ADDRESS = '172.30.22.5'
SUBNET = '172.30.22.0/24'


class DockerMock:
    def __init__(self, address=ADDRESS, extra=None, internal=True):
        self.address=address
        self.extra=extra or {}
        self.internal=internal
        self.calls=[]

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ['docker','network','inspect']:
            return json.dumps([{'Name': NET, 'Id': 'network-id-1', 'Internal':self.internal,
                                'Driver':'bridge','IPAM':{'Config':[{'Subnet': SUBNET}]}}])
        if argv[:3] == ['docker','container','inspect']:
            networks={NET:{'NetworkID':'network-id-1','IPAddress':self.address}}
            networks.update(self.extra)
            return json.dumps(networks)
        if argv[:2] == ['docker','info']:
            return '29.9.2'
        if argv[0] in ('nginx','systemctl'):
            return ''
        raise AssertionError(f'Unexpected subprocess invocation: {argv}')


class IngressSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.available=root/'sites-available'
        self.enabled=root/'sites-enabled'
        self.available.mkdir()
        self.enabled.mkdir()

    def test_doctor_read_only_preflight_without_active_sites(self):
        root=Path(self.tmp.name)/'state'
        root.mkdir()
        result=ingress.diagnose(DockerMock(),self.available,self.enabled,root)
        self.assertTrue(result['private_ingress_preflight'])
        self.assertEqual(result['ingresses_checked'],0)

    def test_doctor_rejects_corrupt_active_state(self):
        root=Path(self.tmp.name)/'state'
        folder=root/'674be29f-4fe2-4d68-b1c9-554e561b8923'
        folder.mkdir(parents=True)
        (folder/'current.json').write_text('{invalid-json')
        with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_STATE_CORRUPT'):
            ingress.diagnose(DockerMock(),self.available,self.enabled,root)

    def test_preflight_validates_docker_and_nginx(self):
        docker=DockerMock()
        self.assertTrue(ingress.preflight(docker,self.available,self.enabled))
        self.assertEqual(docker.calls[-1],['nginx','-t'])

    def test_only_internal_bridge_is_accepted(self):
        with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_NETWORK_NOT_PRIVATE'):
            ingress.container_ipv4(NAME,NET,DockerMock(internal=False))

    def test_no_side_network_accepted(self):
        with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_CONTAINER_NETWORK_INVALID'):
            ingress.container_ipv4(NAME,NET,DockerMock(extra={'bridge':{'IPAddress':'172.17.0.2'}}))

    def test_no_untrusted_container_address(self):
        for address in ('8.8.8.8','127.0.0.1','169.254.2.5','172.30.23.8'):
            with self.subTest(address=address):
                with self.assertRaises(ingress.IngressError):
                    ingress.container_ipv4(NAME,NET,DockerMock(address=address))

    def test_loopback_only_and_unique_probe(self):
        conf=ingress.render_relay(NAME,32456,ADDRESS,8080)
        self.assertIn('listen 127.0.0.1:32456;',conf)
        self.assertIn('proxy_pass http://172.30.22.5:8080;',conf)
        self.assertIn('X-Autokom-Ingress',conf)
        self.assertNotIn('listen 0.0.0.0',conf)
        self.assertNotIn('--publish',conf)

    def test_ingress_create_and_cleanup(self):
        docker=DockerMock()
        with patch.object(ingress,'pick_loopback_port',return_value=33456), \
             patch.object(ingress,'probe_relay',return_value=True):
            state=ingress.create(NAME,NET,8080,execute=docker,available=self.available,enabled=self.enabled)
        self.assertEqual(state['ingress_mode'],ingress.MODE)
        self.assertEqual(state['port'],33456)
        path,link=ingress._layout(NAME,self.available,self.enabled)
        self.assertTrue(path.is_file())
        self.assertTrue(link.is_symlink())
        ingress.remove(NAME,execute=docker,available=self.available,enabled=self.enabled)
        self.assertFalse(path.exists())
        self.assertFalse(link.is_symlink())

    def test_bind_collision_retries_and_fails_closed(self):
        docker=DockerMock()
        with patch.object(ingress,'pick_loopback_port',side_effect=[34567,34568]), \
             patch.object(ingress,'probe_relay',return_value=False), \
             patch.object(ingress.time,'sleep'):
            with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_INGRESS_BIND_FAILED'):
                ingress.create(NAME,NET,8080,execute=docker,available=self.available,enabled=self.enabled,attempts=2)
        path,link=ingress._layout(NAME,self.available,self.enabled)
        self.assertFalse(path.exists())
        self.assertFalse(link.is_symlink())

    def test_reconcile_drift_only_with_matching_approved_config(self):
        docker=DockerMock()
        with patch.object(ingress,'pick_loopback_port',return_value=34567), \
             patch.object(ingress,'probe_relay',return_value=True):
            state=ingress.create(NAME,NET,8080,execute=docker,available=self.available,enabled=self.enabled)
        state.update({'name':NAME,'network':NET})
        docker.address='172.30.22.6'
        self.assertTrue(ingress.reconcile(state,execute=docker,available=self.available,enabled=self.enabled))
        self.assertEqual(state['container_ip'],'172.30.22.6')
        self.assertFalse(ingress.reconcile(state,execute=docker,available=self.available,enabled=self.enabled))

    def test_reconcile_recovers_after_config_updated_before_state_write(self):
        docker=DockerMock()
        with patch.object(ingress,'pick_loopback_port',return_value=34567), \
             patch.object(ingress,'probe_relay',return_value=True):
            state=ingress.create(NAME,NET,8080,execute=docker,available=self.available,enabled=self.enabled)
        state.update({'name':NAME,'network':NET})
        docker.address='172.30.22.6'
        # Simulate a crash exactly after Nginx reload but before state persistence.
        path,_=ingress._layout(NAME,self.available,self.enabled)
        path.write_text(ingress.render_relay(NAME,state['port'],docker.address,8080))
        self.assertTrue(ingress.reconcile(state,execute=docker,available=self.available,enabled=self.enabled))
        self.assertEqual(state['container_ip'],docker.address)

    def test_nginx_failed_reload_cleans_candidate(self):
        docker=DockerMock()
        def fail_reload(argv, **kwargs):
            if argv[:2] == ['systemctl','reload']:
                raise RuntimeError('simulated reload failure')
            return docker(argv,**kwargs)
        with patch.object(ingress,'pick_loopback_port',return_value=34567):
            with self.assertRaises(ingress.IngressError):
                ingress.create(NAME,NET,8080,execute=fail_reload,
                               available=self.available,enabled=self.enabled)
        path,link=ingress._layout(NAME,self.available,self.enabled)
        self.assertFalse(path.exists())
        self.assertFalse(link.is_symlink())

    def test_tampered_ingress_cannot_be_auto_adopted(self):
        docker=DockerMock()
        with patch.object(ingress,'pick_loopback_port',return_value=34567), \
             patch.object(ingress,'probe_relay',return_value=True):
            state=ingress.create(NAME,NET,8080,execute=docker,available=self.available,enabled=self.enabled)
        state.update({'name':NAME,'network':NET})
        path,_=ingress._layout(NAME,self.available,self.enabled)
        path.write_text('server { listen 0.0.0.0:443; }')
        with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_INGRESS_FILE_TAMPERED'):
            ingress.reconcile(state,execute=docker,available=self.available,enabled=self.enabled)

    def test_unmanaged_symlink_cannot_be_overwritten(self):
        docker=DockerMock()
        path,link=ingress._layout(NAME,self.available,self.enabled)
        link.symlink_to(self.available/'another.conf')
        with self.assertRaises(ingress.IngressError):
            ingress.create(NAME,NET,8080,execute=docker,available=self.available,enabled=self.enabled)


if __name__=='__main__':
    unittest.main()
