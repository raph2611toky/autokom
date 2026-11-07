"""GCP HTTP-01 checks the routed NIC, never just localhost.

Historical failure: AUTOKOM loopback probe got 404 even though the GCP
private-NIC vhost could serve the exact token. The scenario is recreated
with address-specific Nginx listeners in a fully isolated config.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as obs


class GcpIngressLocalRoute(unittest.TestCase):
    def test_selects_gcp_ens4_ipv4_from_routing_table(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps([{'dev': 'ens4', 'prefsrc': '10.128.0.25'}]))
        with patch.object(obs.subprocess, 'run', return_value=result) as cmd:
            self.assertEqual(obs._acme_ingress_local_ipv4(), '10.128.0.25')
        cmd.assert_called_once()
        self.assertEqual(cmd.call_args.args[0][:3], ['ip', '-j', '-4'])

    def test_rejects_docker_and_wireguard_and_loopback(self):
        for interface, address in [('docker0','172.17.0.1'),('wg0','10.0.0.2'),('ens4','127.0.0.1')]:
            with self.subTest(interface=interface):
                result = SimpleNamespace(returncode=0, stdout=json.dumps([{'dev': interface,'src': address}]))
                with patch.object(obs.subprocess,'run',return_value=result):
                    self.assertIsNone(obs._acme_ingress_local_ipv4())

    def test_routed_nic_200_allows_loopback_404_and_cleans_token(self):
        with TemporaryDirectory() as tmp:
            challenge = Path(tmp) / 'acme' / '.well-known' / 'acme-challenge'
            challenge.mkdir(parents=True)
            checked=[]
            def probe(ip, domain, uri, marker):
                checked.append((ip, domain, uri))
                path = challenge / uri.rsplit('/',1)[1]
                self.assertEqual(path.read_text(), marker)
                return (200,True) if ip == '10.128.0.25' else (404,False)
            with patch.object(obs,'ACME_WEBROOT',Path(tmp)/'acme'), \
                 patch.object(obs,'_acme_ingress_local_ipv4',return_value='10.128.0.25'), \
                 patch.object(obs,'_acme_probe',side_effect=probe),patch.object(obs,'out') as output:
                obs.check_acme_http01_local('prometheus.ikom.mg')
            self.assertEqual([x[0] for x in checked],['10.128.0.25','127.0.0.1'])
            self.assertEqual(list(challenge.iterdir()),[])
            self.assertTrue(any('Loopback' in x.args[1] for x in output.call_args_list))

    def test_loopback_success_does_not_hide_routed_nic_404(self):
        with TemporaryDirectory() as tmp:
            challenge = Path(tmp) / 'acme' / '.well-known' / 'acme-challenge'
            challenge.mkdir(parents=True)
            def probe(ip, *args):
                return (404,False) if ip == '10.128.0.25' else (200,True)
            with patch.object(obs,'ACME_WEBROOT',Path(tmp)/'acme'), \
                 patch.object(obs,'_acme_ingress_local_ipv4',return_value='10.128.0.25'), \
                 patch.object(obs,'_acme_probe',side_effect=probe),patch.object(obs,'out'):
                with self.assertRaisesRegex(obs.SetupError,'10.128.0.25: HTTP 404'):
                    obs.check_acme_http01_local('prometheus.ikom.mg')
            self.assertEqual(list(challenge.iterdir()),[])

    def test_without_route_uses_loopback(self):
        with TemporaryDirectory() as tmp:
            challenge = Path(tmp) / 'acme' / '.well-known' / 'acme-challenge'
            challenge.mkdir(parents=True)
            with patch.object(obs,'ACME_WEBROOT',Path(tmp)/'acme'), \
                 patch.object(obs,'_acme_ingress_local_ipv4',return_value=None), \
                 patch.object(obs,'_acme_probe',return_value=(200,True)) as probe, patch.object(obs,'out'):
                obs.check_acme_http01_local('prometheus.ikom.mg')
            self.assertEqual(probe.call_args.args[0],'127.0.0.1')
            self.assertEqual(list(challenge.iterdir()),[])

    def test_nested_private_ancestor_fails_before_certbot(self):
        if os.geteuid() != 0:
            self.skipTest('AUTOKOM installer requires root')
        with TemporaryDirectory() as tmp:
            outer = Path(tmp)
            outer.chmod(0o755)
            private = outer / 'restricted'
            private.mkdir(mode=0o700)
            webroot = private / 'acme'
            with patch.object(obs, 'ACME_WEBROOT', webroot):
                with self.assertRaisesRegex(obs.SetupError, 'non traversable par Nginx'):
                    obs.ensure_acme_webroot()
            self.assertFalse(webroot.exists())
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)

    def test_proxy_from_setup_proxy_safely_adapts_to_observability(self):
        prom = obs.vhost_content('prometheus.ikom.mg',9090,prometheus=True,gated=False)
        self.assertIn('upstream autokom_prometheus_9090',prom)
        self.assertIn('server 127.0.0.1:9090;',prom)
        self.assertIn('auth_basic_user_file',prom)
        self.assertIn('proxy_set_header X-Forwarded-Host $host;',prom)
        self.assertIn('proxy_set_header X-Forwarded-Port $server_port;',prom)
        self.assertNotIn('proxy_set_header Upgrade',prom)
        self.assertIn('listen 443 ssl;',prom)
        graf = obs.vhost_content('grafana.ikom.mg',3000,gated=False)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;',graf)
        self.assertIn('proxy_read_timeout 3600s;',graf)
        self.assertIn('proxy_buffering off;',graf)
        old = obs.vhost_content('grafana.ikom.mg',3000,gated=False,hardened_proxy=False)
        self.assertNotIn('proxy_read_timeout 3600s;',old)
        self.assertIn('proxy_pass http://autokom_grafana_3000;',old)


class NginxAddressSpecificRealIntegration(unittest.TestCase):
    def test_loopback_404_but_other_local_ip_200_with_same_webroot(self):
        if not shutil.which('nginx'):
            self.skipTest('Nginx absent from test machine')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            folder.chmod(0o755)
            webroot=folder/'acme'
            path=webroot/'.well-known'/'acme-challenge'
            path.mkdir(parents=True)
            (path/'test-token').write_text('public-nic-200')
            conf=folder/'nginx.conf'
            conf.write_text(f'''worker_processes 1;
pid {folder}/nginx.pid;
events {{ worker_connections 128; }}
http {{
 access_log {folder}/access.log;
 error_log {folder}/error.log notice;
 server {{ listen 127.0.0.1:{port} default_server; server_name loopback-only; location / {{ return 404; }} }}
 server {{ listen {port}; server_name prometheus.ikom.mg;
    location ^~ /.well-known/acme-challenge/ {{ root {webroot}; default_type text/plain; try_files $uri =404; }}
    location / {{ return 503; }}
 }}
}}
''')
            check=subprocess.run(['nginx','-t','-c',str(conf)],capture_output=True,text=True)
            if check.returncode:
                self.skipTest('Nginx integration config unsupported: ' + check.stderr[:150])
            process=subprocess.Popen(['nginx','-c',str(conf),'-g','daemon off;'],
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(30):
                    if process.poll() is not None:
                        self.skipTest('Nginx isolated worker could not start')
                    try:
                        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=0.5)
                        conn.request('GET','/',headers={'Host':'prometheus.ikom.mg'})
                        conn.getresponse().read(); conn.close()
                        break
                    except OSError:
                        time.sleep(0.05)
                response=[]
                for address in ('127.0.0.1','127.0.0.2'):
                    c=http.client.HTTPConnection(address,port,timeout=3)
                    c.request('GET','/.well-known/acme-challenge/test-token',headers={'Host':'prometheus.ikom.mg'})
                    r=c.getresponse()
                    response.append((r.status,r.read()))
                    c.close()
                self.assertEqual(response[0][0],404)
                self.assertEqual(response[1],(200,b'public-nic-200'))
            finally:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill();process.wait()

if __name__=='__main__':
    unittest.main()
