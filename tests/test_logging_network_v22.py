"""v22: fail-closed network supervision; no Docker daemon required."""
from pathlib import Path
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import logging_stack as ls
import logging_health as lh
import tools_registry as tr

class V22NetworkRepair(unittest.TestCase):
    def test_private_bridge_preserves_internal_communication(self):
        c=yaml.safe_load(ls.compose('both'))
        self.assertTrue(c['networks']['logging']['internal'])
        self.assertEqual(c['networks']['loopback']['driver'],'bridge')
        self.assertEqual(c['networks']['loopback']['driver_opts']['com.docker.network.bridge.host_binding_ipv4'],'127.0.0.1')
        for name,port in [('loki',3100),('alloy',12345),('opensearch',9200),('opensearch-dashboards',5601)]:
            service=c['services'][name]
            self.assertEqual(service['networks'],['logging','loopback'])
            self.assertEqual(service['ports'],[f'127.0.0.1:{port}:{port}'])
        self.assertEqual(c['services']['data-prepper']['networks'],['logging'])
        self.assertNotIn('ports',c['services']['data-prepper'])

    def test_running_without_endpoint_not_ready(self):
        with patch.object(ls.shutil,'which',return_value='/usr/bin/docker'),\
             patch.object(ls.Path,'exists',return_value=True),\
             patch.object(ls,'run',return_value=subprocess.CompletedProcess([],0,'alloy\nloki\n','')),\
             patch.object(lh,'http_status',side_effect=lambda x: {'ok': x=='alloy'}):
            self.assertFalse(ls.ready('loki'))
        with patch.object(ls.shutil,'which',return_value='/usr/bin/docker'),\
             patch.object(ls.Path,'exists',return_value=True),\
             patch.object(ls,'run',return_value=subprocess.CompletedProcess([],0,'alloy\nloki\n','')),\
             patch.object(lh,'http_status',return_value={'ok':True}):
            self.assertTrue(ls.ready('loki'))

    def test_inspect_reports_no_port_despite_running_container(self):
        output = [subprocess.CompletedProcess([],0,'fc00af01\n',''),
                  subprocess.CompletedProcess([],0,'{"3100/tcp":null}\n','')]
        with patch.object(lh.shutil,'which',return_value='/usr/bin/docker'),\
             patch.object(lh,'safe_process',side_effect=output):
            state,mappings=lh.published_ports('loki')
        self.assertEqual(state,'aucune publication')
        self.assertEqual(mappings,())

    def test_inspect_rejects_non_loopback_publications(self):
        output = [subprocess.CompletedProcess([],0,'abc123\n',''),
                  subprocess.CompletedProcess([],0,json.dumps({'3100/tcp':[{'HostIp':'0.0.0.0','HostPort':'3100'}]}),'')]
        with patch.object(lh.shutil,'which',return_value='/usr/bin/docker'),\
             patch.object(lh,'safe_process',side_effect=output),\
             patch.object(lh,'http_status',return_value={'ok':True,'detail':'ready'}):
            rows=lh.runtime_rows('loki')
        self.assertTrue(any(k=='ALERTE RÉSEAU' for k,v in rows))

    def test_show_logs_has_endpoint_metrics_real_port_and_docker_state(self):
        with patch.object(tr,'service_state',return_value='active'),\
             patch.object(lh,'published_ports',return_value=('publié',('127.0.0.1:3100 → 3100/tcp',))),\
             patch.object(lh,'http_status',return_value={'ok':True,'detail':'HTTP 200 — prêt'}),\
             patch('logging_runtime.detected',return_value='docker'),\
             patch.object(tr.Path,'exists',return_value=False):
            with redirect_stdout(io.StringIO()) as f:
                tr.show('loki')
        out=f.getvalue()
        self.assertIn('127.0.0.1:3100',out)
        self.assertIn('/metrics',out)
        self.assertIn('Publication Docker',out)
        self.assertIn('HTTP 200',out)

    def test_alloy_metrics_and_readiness_endpoints(self):
        a=lh.SERVICES['alloy']
        self.assertEqual(a['ready'],'/-/ready')
        self.assertEqual(a['metrics'],'/metrics')
        self.assertEqual(a['port'],12345)

if __name__=='__main__':unittest.main()
