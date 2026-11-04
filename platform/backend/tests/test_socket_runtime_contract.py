"""Static security-contract tests: can run without a Django installation."""
from pathlib import Path
import ast
import unittest

ROOT=Path(__file__).resolve().parents[1]

class RuntimeContractTests(unittest.TestCase):
    def test_all_python_files_parse(self):
        folders=['apps/deployments/runs','apps/deployments/domains']
        for folder in folders:
            for path in (ROOT/folder).rglob('*.py'):
                with self.subTest(path=path):ast.parse(path.read_text(encoding='utf-8'))

    def test_no_shell_in_web_runtime(self):
        for path in [ROOT/'apps/deployments/runs/jenkins.py',
                     ROOT/'apps/deployments/runs/autokom_socket.py',
                     ROOT/'apps/deployments/runs/worker.py',
                     ROOT/'apps/deployments/domains/services.py']:
            text=path.read_text()
            with self.subTest(path=path):
                self.assertNotIn('subprocess.',text)
                self.assertNotIn('os.system(',text)
                self.assertNotIn('shell=True',text)
                self.assertNotIn('docker.sock',text)

    def test_one_verb_per_domain_view(self):
        tree=ast.parse((ROOT/'apps/deployments/domains/views.py').read_text())
        for clazz in (x for x in tree.body if isinstance(x,ast.ClassDef) and x.name.endswith('APIView')):
            methods=[x.name for x in clazz.body if isinstance(x,ast.FunctionDef) and x.name in ('get','post','put','patch','delete')]
            self.assertEqual(len(methods),1,clazz.name)
            self.assertTrue(any(isinstance(node,ast.Try) for node in ast.walk(clazz)))

    def test_signed_runner_manifest_no_secrets_by_default(self):
        text=(ROOT/'apps/deployments/runs/runner_manifest.py').read_text()
        self.assertIn('include_secrets=False',text)
        self.assertIn("if include_secrets else {}",text)

    def test_ha_remains_disabled(self):
        text=(ROOT/'apps/deployments/runs/services.py').read_text()
        self.assertIn('non activable avant le cluster GCP',text)
        self.assertNotIn('AUTOKOM_IMAGE_REGISTRY requis',text)

    def test_no_javascript_client_control(self):
        text=(ROOT/'apps/deployments/domains/services.py').read_text()
        self.assertIn('get_environment(user,project_id,environment_id,write=True)',text)

if __name__=='__main__':unittest.main()
