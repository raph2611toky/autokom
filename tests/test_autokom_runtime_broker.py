"""Fast tests for Core's non-shell, local-IPC, Docker and Nginx boundaries."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
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
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(broker,'STATE',Path(directory)/'client-runtime'):
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

    def test_runtime_preflight_uses_tool_specific_version_flags(self):
        role=(ROOT/'ansible/roles/platform_runtime/tasks/main.yml').read_text()
        self.assertIn('name: Vérifier Docker sans le réinstaller',role)
        self.assertIn('argv: [docker, --version]',role)
        self.assertIn('name: Vérifier Nginx sans le réinstaller',role)
        self.assertIn('argv: [nginx, -v]',role)
        self.assertIn('name: Vérifier Certbot sans le réinstaller',role)
        self.assertIn('argv: [certbot, --version]',role)
        self.assertEqual(role.count('check_mode: false'),3)
        self.assertNotIn('{{ item }} --version',role)

    def test_runtime_preview_does_not_start_uninstalled_systemd_units(self):
        role=(ROOT/'ansible/roles/platform_runtime/tasks/main.yml').read_text()
        # Broker, worker and the autonomous DNS timer must remain check-mode safe.
        self.assertEqual(role.count('when: not ansible_check_mode'), 4)
        self.assertIn('name: autokom-domain-reconciler.timer', role)

    def test_backend_runtime_contract_has_shared_runner_key_and_managed_suffix(self):
        settings=(ROOT/'platform/backend/config/settings.py').read_text()
        manager=(ROOT/'scripts/lib/autokom_app_manager.py').read_text()
        project=(ROOT/'platform/backend/apps/projects/project/services.py').read_text()
        domain=(ROOT/'platform/backend/apps/deployments/domains/services.py').read_text()
        self.assertIn('AUTOKOM_RUNNER_SHARED_KEY',manager)
        self.assertIn('AUTOKOM_MANAGED_DOMAIN_SUFFIX',manager)
        self.assertIn('AUTOKOM_MANAGED_DOMAIN_SUFFIX',settings)
        self.assertIn('domain.verification_token_hash =',project)
        self.assertIn('settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX',domain)

    def test_runtime_setup_check_runs_ansible_check_mode(self):
        from unittest.mock import Mock
        with patch.object(admin.subprocess,'run',return_value=Mock(returncode=0,stderr='')) as syntax:
            with patch.object(admin,'run_live') as preview:
                admin.setup(apply=False)
        self.assertIn('--syntax-check',syntax.call_args.args[0])
        command=preview.call_args.args[0]
        self.assertIn('--check',command)
        self.assertEqual(command[-1],str(ROOT/'ansible/playbooks/platform-runtime.yml'))
        self.assertEqual(preview.call_args.kwargs['env']['AUTOKOM_CHECK_MODE'],'1')

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
