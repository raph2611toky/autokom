"""Fast tests for Core's non-shell, local-IPC, Docker and Nginx boundaries."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def load(path, name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

broker=load('scripts/lib/autokom_platform_broker.py','autokom_broker_test')
admin=load('scripts/lib/autokom_runtime_admin.py','autokom_runtime_admin_test')

class BrokerTests(unittest.TestCase):
    def test_reject_shell_api(self):
        with self.assertRaises(broker.BrokerError):
            broker.handle('exec',{'shell':'rm -rf /'})

    def test_host_validation(self):
        for bad in ['localhost','abc; rm -rf','_bad.example.com','127.0.0.1','bad.example.local',None,'a'*64+'.example.com']:
            with self.subTest(bad=bad),self.assertRaises(broker.BrokerError):
                broker.valid_hostname(bad)
        self.assertEqual(broker.valid_hostname('example.ikom.mg'),'example.ikom.mg')

    def test_secret_filter(self):
        with self.assertRaises(broker.BrokerError):broker._safe_env({'AUTOKOM_RUNNER_SHARED_KEY':'danger'})
        with self.assertRaises(broker.BrokerError):broker._safe_env({'ABC':'a\nb'})
        self.assertEqual(broker._safe_env({'DATABASE_URL':'mysql://local'}),{'DATABASE_URL':'mysql://local'})

    def test_reject_ha_before_docker(self):
        p={'environment_id':'a'*8+'-'+ 'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
           'run_id':'a'*8+'-'+ 'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
           'cpu_millicores':500,'memory_mb':512,'storage_mb':512,'internal_port':8000,
           'replicas':2,'healthcheck':'/'}
        with self.assertRaises(broker.BrokerError):broker._docker_deploy(p)

    def test_nginx_vhost_not_arbitrary_upstream(self):
        with self.assertRaises(broker.BrokerError):broker._nginx_config('app.ikom.mg',22)
        vhost=broker._nginx_config('app.ikom.mg',21300,tls=True)
        self.assertIn('proxy_pass http://127.0.0.1:21300;',vhost)
        self.assertIn('ssl_certificate /etc/letsencrypt/live/app.ikom.mg/',vhost)
        self.assertNotIn('proxy_pass http://0.0.0.0:',vhost)

    def test_external_domain_has_txt_and_ip(self):
        with patch.dict(broker.os.environ,{'AUTOKOM_INGRESS_IPV4':'56.228.86.160'}),patch.object(broker,'_dig',side_effect=lambda kind,host: {'TXT':['autokom-verification='+'x'*64], 'A':['56.228.86.160']}[kind]):
            self.assertTrue(broker._check_dns('example.ikom.mg','x'*64,False)['verified'])
            with self.assertRaises(broker.BrokerError):broker._check_dns('example.ikom.mg','y'*64,False)

    def test_managed_zone_restricted(self):
        with patch.dict(broker.os.environ,{'AUTOKOM_INGRESS_IPV4':'56.228.86.160','AUTOKOM_MANAGED_DOMAIN_SUFFIX':'apps.auto.ik0m.tech'}),patch.object(broker,'_dig',return_value=['56.228.86.160']):
            self.assertTrue(broker._check_dns('site.apps.auto.ik0m.tech',None,True)['verified'])
            with self.assertRaises(broker.BrokerError):broker._check_dns('site.attacker.com',None,True)

    def test_job_sync_xml_is_sandboxed(self):
        xml=admin.pipeline_xml("pipeline { agent none }")
        self.assertIn(b"<sandbox>true</sandbox>",xml)
        self.assertIn(b'AUTOKOM_RUN_ID',xml)

    def test_runner_does_not_mount_docker_socket(self):
        runner=(ROOT/'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        self.assertIn('autokom-isolated',runner)
        self.assertNotIn('docker.sock',runner)
        self.assertNotIn('AUTOKOM_IMAGE_REGISTRY',runner)

    def test_broker_socket_permission(self):
        text=(ROOT/'scripts/lib/autokom_platform_broker.py').read_text()
        self.assertIn('SO_PEERCRED',text)
        self.assertIn('os.chmod(SOCKET,0o660)',text)
        self.assertNotIn('shell=True',text)

if __name__=='__main__':unittest.main()
