"""Regression checks for the opt-in stack DNS and fixed backend port contract."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'/'runtime'))
sys.path.insert(0,str(ROOT/'scripts'/'lib'))
from autokom_backend_recipes import backend_recipe, BACKEND_RUNTIME

class StackDnsPortTests(unittest.TestCase):
    def config(self, profile, port):
        return {'runtime':BACKEND_RUNTIME[profile], 'port':port,
                'build_command':'','start_command':'','output_directory':''}

    def test_stack_express_receives_port_8000(self):
        with tempfile.TemporaryDirectory() as dirname:
            path=pathlib.Path(dirname)
            (path/'package.json').write_text(json.dumps({'dependencies':{'express':'*'},'scripts':{'start':'node src/server.js'}}))
            (path/'package-lock.json').write_text('{}')
            dockerfile,_=backend_recipe('node_express',path,self.config('node_express',8000))
            self.assertIn('PORT=8000',dockerfile)
            self.assertIn('EXPOSE 8000',dockerfile)
            legacy,_=backend_recipe('node_express',path,self.config('node_express',8080))
            self.assertIn('PORT=8080',legacy)
            self.assertIn('EXPOSE 8080',legacy)

    def test_python_and_php_stack_ports(self):
        with tempfile.TemporaryDirectory() as dirname:
            path=pathlib.Path(dirname)
            (path/'requirements.txt').write_text('Flask==3.1.0')
            (path/'app.py').write_text('app=None')
            dockerfile,_=backend_recipe('python_flask',path,self.config('python_flask',8000))
            self.assertIn('0.0.0.0:8000',dockerfile)
            self.assertIn('EXPOSE 8000',dockerfile)
            (path/'index.php').write_text('<?php echo "hi";')
            dockerfile,files=backend_recipe('php_plain',path,self.config('php_plain',8000))
            self.assertIn('EXPOSE 8000',dockerfile)
            self.assertIn('listen 8000',files['autokom-php-nginx.conf'])

    def test_still_rejects_arbitrary_ports(self):
        with tempfile.TemporaryDirectory() as dirname:
            path=pathlib.Path(dirname)
            (path/'requirements.txt').write_text('Flask==3.1.0')
            (path/'app.py').write_text('app=None')
            with self.assertRaises(ValueError):
                backend_recipe('python_flask',path,self.config('python_flask',4000))

    def test_dns_proof_is_server_side_and_old_deployments_unchanged(self):
        source=(ROOT/'platform/backend/apps/projects/configurations/stack_services.py').read_text()
        views=(ROOT/'platform/backend/apps/projects/configurations/stack_views.py').read_text()
        broker=(ROOT/'scripts/lib/autokom_platform_broker.py').read_text()
        self.assertIn('preflight_stack_domains(stack)',source)
        self.assertIn("values['PORT'] = '8000'",source)
        self.assertIn("values['FRONTEND_ORIGIN'] = 'https://' + api_url",source)
        self.assertIn("'stack_hostname': data['hostname']",source)
        self.assertIn("'domain.verify'",source)
        self.assertIn("'domain.dns.create'",source)
        self.assertIn('StackDomainPreflightAPIView',views)
        self.assertIn("back_state.get('internal_port', 8080)",broker)
        self.assertIn("doc['gateway_hostname'] = backend_hostname",broker)

if __name__=='__main__':unittest.main()
