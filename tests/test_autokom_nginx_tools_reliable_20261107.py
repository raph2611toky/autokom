"""Regression: TLS issued but external default page, local false-success and missing logs."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
import contextlib
import io
import json
import os
import sys
import socket
import shutil
import subprocess
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import autokom_ingress_health as ingress
import autokom_tools_diagnostics as diag
import autokom_cli as cli
import tools_registry as registry

STOCK='''server {
listen 80 default_server;
listen [::]:80 default_server;
server_name _;
root /var/www/html;
location / { try_files $uri $uri/ =404; }
}
'''

class DefaultRouter(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.avail=self.base/'sites-available';self.ena=self.base/'sites-enabled'
        self.avail.mkdir();self.ena.mkdir()
        (self.avail/'default').write_text(STOCK)
        (self.ena/'default').symlink_to(self.avail/'default')
        self.patch_available=mock.patch.object(ingress,'NGINX_AVAILABLE',self.avail)
        self.patch_available.start()
        self.addCleanup(self.patch_available.stop)
        self.addCleanup(self.tmp.cleanup)
    def simulated(self, results):
        return mock.patch.object(ingress,'_run',side_effect=[SimpleNamespace(returncode=rc, stdout=out,stderr='') for rc,out in results])
    def test_recognizes_stock_without_deleting_source(self):
        self.assertTrue(ingress._stock_default(self.ena/'default'))
        self.assertTrue((self.avail/'default').exists())
    def test_custom_reverse_proxy_not_touched(self):
        (self.avail/'default').write_text(STOCK+'\nproxy_pass http://127.0.0.1:5000;')
        with self.simulated([(0,'')]):
            with self.assertRaisesRegex(ingress.IngressError,'personnalisé'):
                ingress.reconcile_catchall(enabled_dir=self.ena,available_dir=self.avail)
        self.assertTrue((self.ena/'default').is_symlink())
    def test_apply_disables_only_stock_symlink_and_catches_unknown_names(self):
        nginx_dump=f'''# configuration file {self.ena/'default'}:\n{STOCK}\n'''
        with self.simulated([(0,''),(0,nginx_dump),(0,''),(0,'')]),mock.patch.object(ingress.os,'geteuid',return_value=0):
            self.assertIn('activé', ingress.reconcile_catchall(enabled_dir=self.ena,available_dir=self.avail))
        self.assertTrue((self.avail/'default').is_file())
        self.assertFalse((self.ena/'default').exists())
        self.assertTrue(ingress.catchall_ready(enabled=self.ena/ingress.CATCHALL_NAME,
                                               available=self.avail/ingress.CATCHALL_NAME,
                                               default=self.ena/'default'))
        self.assertIn('return 404', (self.avail/ingress.CATCHALL_NAME).read_text())
        self.assertIn('ssl_reject_handshake on', (self.avail/ingress.CATCHALL_NAME).read_text())
    def test_check_is_read_only(self):
        with self.simulated([(0,''),(0,'')]):
            self.assertIn('à créer',ingress.reconcile_catchall(check=True,enabled_dir=self.ena,available_dir=self.avail))
        self.assertTrue((self.ena/'default').is_symlink())
        self.assertFalse((self.avail/ingress.CATCHALL_NAME).exists())
    def test_nginx_test_failure_rolls_back_and_keeps_vendor_default(self):
        with self.simulated([(0,''),(0,''),(1,''),(0,''),(0,'')]), mock.patch.object(ingress.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(ingress.IngressError,'nginx -t refuse'):
                ingress.reconcile_catchall(enabled_dir=self.ena,available_dir=self.avail)
        self.assertTrue((self.ena/'default').is_symlink())
        self.assertFalse((self.avail/ingress.CATCHALL_NAME).exists())
    def test_reload_failure_rolls_back_and_keeps_vendor_default(self):
        with self.simulated([(0,''),(0,''),(0,''),(1,''),(0,''),(0,'')]), mock.patch.object(ingress.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(ingress.IngressError,'rechargement'):
                ingress.reconcile_catchall(enabled_dir=self.ena,available_dir=self.avail)
        self.assertTrue((self.ena/'default').is_symlink())
        self.assertFalse((self.ena/ingress.CATCHALL_NAME).exists())
    def test_refuses_another_default_server(self):
        dump='# configuration file /etc/nginx/custom.conf:\nlisten 80 default_server;'
        with self.simulated([(0,''),(0,dump)]):
            with self.assertRaisesRegex(ingress.IngressError,'default_server'):
                ingress.reconcile_catchall(enabled_dir=self.ena,available_dir=self.avail)
        self.assertTrue((self.ena/'default').is_symlink())

class DomainReadiness(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name)
    def test_secure_saved_domains_used_when_ini_empty(self):
        state=self.dir/'state.json';state.write_text(json.dumps({'schema':1,'prometheus_domain':'prometheus.ikom.mg'}))
        os.chmod(state,0o600)
        with mock.patch.object(ingress.os,'geteuid',return_value=0):
            names=ingress.managed_domains(SimpleNamespace(prometheus_domain='',grafana_domain=''),state_path=state,selection_path=self.dir/'missing')
        self.assertEqual(names['prometheus'],'prometheus.ikom.mg')
    def test_insecure_or_symlink_state_cannot_inject_domain(self):
        state=self.dir/'state.json';state.write_text(json.dumps({'schema':1,'prometheus_domain':'attack.example'}));os.chmod(state,0o644)
        names=ingress.managed_domains(SimpleNamespace(prometheus_domain='',grafana_domain=''),state_path=state,selection_path=self.dir/'missing')
        self.assertEqual(names['prometheus'],'')
        sym=self.dir/'sym';sym.symlink_to(state)
        self.assertFalse(ingress.managed_domains(SimpleNamespace(prometheus_domain='',grafana_domain=''),state_path=sym,selection_path=self.dir/'missing')['prometheus'])
    def test_explicit_local_only_overrides_older_domain(self):
        state=self.dir/'state.json'; state.write_text(json.dumps({'schema':1,'prometheus_domain':'old.example'}));os.chmod(state,0o600)
        selection=self.dir/'selection.json';selection.write_text(json.dumps({'schema':1,'prometheus_domain':''}));os.chmod(selection,0o600)
        names=ingress.managed_domains(SimpleNamespace(prometheus_domain='ini.example',grafana_domain=''),state_path=state,selection_path=selection)
        self.assertEqual(names['prometheus'],'')
    def test_expected_codes(self):
        self.assertTrue(ingress.public_vhost_healthy('prometheus.ikom.mg','prometheus',probe=lambda x:(401,'127.0.0.1',()),http_probe=lambda x:(301,'127.0.0.1',())))
        self.assertFalse(ingress.public_vhost_healthy('prometheus.ikom.mg','prometheus',probe=lambda x:(200,'127.0.0.1',()),http_probe=lambda x:(301,'127.0.0.1',())))
        self.assertFalse(ingress.public_vhost_healthy('grafana.ikom.mg','grafana',probe=lambda x:(302,'127.0.0.1',()),http_probe=lambda x:(200,'127.0.0.1',())))
        self.assertTrue(ingress.public_vhost_healthy('grafana.ikom.mg','grafana',probe=lambda x:(302,'127.0.0.1',()),http_probe=lambda x:(308,'127.0.0.1',())))
    def test_final_verification_fails_on_default_welcome(self):
        with self.assertRaisesRegex(ingress.IngressError,'redirige'):
            ingress.verify_observability([('grafana','grafana.ikom.mg')],probe=lambda _: (302,'127.0.0.1',()), http_probe=lambda _: (200,'127.0.0.1',()))
    def test_final_verification_fails_on_no_tls_listener(self):
        with self.assertRaisesRegex(ingress.IngressError,'HTTPS/SNI'):
            ingress.verify_observability([('prometheus','prometheus.ikom.mg')],probe=lambda _: (0,'',('127.0.0.1: ConnectionRefusedError',)),http_probe=lambda _: (301,'127.0.0.1',()))
    def test_snapshot_domains_with_private_state(self):
        s=SimpleNamespace(prometheus_domain='prometheus.ikom.mg',grafana_domain='',prometheus_port=9090,grafana_port=3000)
        with mock.patch.object(ingress,'catchall_ready',return_value=True),\
             mock.patch.object(ingress,'public_vhost_healthy',return_value=False),\
             mock.patch('observability.existing_vhosts',return_value=['vhost']),\
             mock.patch('observability.vhost_target_ok',return_value=True),\
             mock.patch('observability.valid_cert',return_value=True):
            self.assertFalse(cli.domains_ok(s))

class DiagnosticCommands(unittest.TestCase):
    def test_parser_supports_status_and_logs(self):
        p=cli.make_parser()
        self.assertEqual(p.parse_args(['tools','status']).tool_cmd,'status')
        v=p.parse_args(['tools','prometheus','logs','--lines','37','--since','2 hours ago'])
        self.assertEqual((v.file_action,v.lines,v.since),('logs',37,'2 hours ago'))
        self.assertEqual(p.parse_args(['tools','grafana','status']).file_action,'status')
        self.assertTrue(p.parse_args(['tools','nginx','logs','--follow']).follow)
    def test_log_command_safe_no_shell(self):
        with mock.patch.object(diag,'_call',return_value=0) as c:
            diag.service_logs('prometheus',lines=100,since='1 hour ago')
        self.assertEqual(c.call_args.args[0],['journalctl','-u','prometheus','--no-pager','--output=cat','-n','100','--since','1 hour ago'])
    def test_reject_unbounded_line_count(self):
        with self.assertRaisesRegex(diag.DiagnosticsError,'--lines'):
            diag.service_logs('jenkins',lines=9999)
    def test_log_redaction(self):
        sample='Authorization: Bearer SECRET123 password=hunter2 api_token=tokenx'
        clean=diag._redact(sample)
        for secret in ('SECRET123','hunter2','tokenx'):
            self.assertNotIn(secret,clean)
    def test_tools_status_dispatch(self):
        with mock.patch.object(diag,'overview',return_value=0):
            self.assertEqual(cli.main(['tools','status']),0)
    def test_tools_prometheus_logs_dispatch(self):
        with mock.patch.object(diag,'service_logs',return_value=0) as logs:
            self.assertEqual(cli.main(['tools','prometheus','logs','--lines','50']),0)
            logs.assert_called_once_with('prometheus',lines=50,follow=False,since=None)
    def test_legacy_file_commands_unchanged(self):
        with mock.patch('tool_files.dispatch_tool_files',return_value=None) as legacy:
            self.assertEqual(cli.main(['tools','prometheus','files']),0)
            legacy.assert_called_once()

class RealNginx(unittest.TestCase):
    @unittest.skipUnless(shutil.which('nginx'), 'Nginx absent')
    def test_exact_template_passes_nginx_syntax(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));tlsport=sock.getsockname()[1]
            conf=(ingress.CATCHALL
                  .replace('listen 80 default_server;',f'listen 127.0.0.1:{port} default_server;')
                  .replace('listen [::]:80 default_server;','')
                  .replace('listen 443 ssl default_server;',f'listen 127.0.0.1:{tlsport} ssl default_server;')
                  .replace('listen [::]:443 ssl default_server;',''))
            conf_path=root/'nginx.conf'
            conf_path.write_text(f'pid {root}/nginx.pid;\nevents {{ worker_connections 64; }}\nhttp {{\n{conf}\n}}\n')
            result=subprocess.run(['nginx','-p',str(root)+'/','-c',str(conf_path),'-t'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('nginx'), 'Nginx absent')
    def test_default_404_but_named_host_redirects(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'logs').mkdir()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            # Run Nginx with a FULLY isolated config on an ephemeral port.
            conf=root/'nginx.conf'
            conf.write_text(f"""daemon off;
pid {root}/nginx.pid;
error_log {root}/logs/error.log;
events {{ worker_connections 64; }}
http {{
    access_log off;
    server {{ listen 127.0.0.1:{port} default_server; server_name _; return 404; }}
    server {{ listen 127.0.0.1:{port}; server_name prometheus.ikom.mg; return 301 https://$host$request_uri; }}
}}
""")
            check=subprocess.run(['nginx','-p',str(root)+'/','-c',str(conf),'-t'],capture_output=True,text=True)
            self.assertEqual(check.returncode,0,check.stderr)
            proc=subprocess.Popen(['nginx','-p',str(root)+'/','-c',str(conf)], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.22)
                def request(host):
                    with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
                        client.sendall(f'HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
                        return client.recv(256).splitlines()[0]
                self.assertIn(b'404',request('unknown.ikom.mg'))
                self.assertIn(b'301',request('prometheus.ikom.mg'))
            finally:
                proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)

if __name__=='__main__':unittest.main()
