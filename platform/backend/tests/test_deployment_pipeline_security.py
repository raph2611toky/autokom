"""Stdlib-only checks: run with python3 -m unittest discover -s tests -p 'test_deployment_pipeline_security.py'.
Full Django + MySQL integration must run in AUTOKOM's configured dev test database.
"""
import ast
import base64
import hashlib
import hmac
import importlib.util
import os
from pathlib import Path
import re
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
CORE = BASE.parent.parent
AGENT = CORE / 'scripts/runtime/autokom_agent_local.py'
spec = importlib.util.spec_from_file_location('autokom_trusted_agent_test', AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

class PipelineSecurityTests(unittest.TestCase):
    def test_ast_compile_all_changed_paths(self):
        for directory in ('apps/deployments/runs', 'apps/plans/entitlements', 'deployment_templates'):
            for f in (BASE/directory).rglob('*.py'):
                with self.subTest(path=f.relative_to(BASE)):
                    ast.parse(f.read_text(encoding='utf-8'), filename=str(f))

    def test_each_apiview_has_only_one_http_method_and_try_except(self):
        tree = ast.parse((BASE/'apps/deployments/runs/views.py').read_text())
        for cls in [n for n in tree.body if isinstance(n,ast.ClassDef) and n.name.endswith('APIView')]:
            methods = [n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in
                       ('get','post','put','patch','delete')]
            with self.subTest(view=cls.name):
                self.assertEqual(1,len(methods))
                self.assertTrue(any(isinstance(n,ast.Try) for n in ast.walk(methods[0])))

    def test_separate_swagger_per_endpoint(self):
        modules = ['preview','enqueue','list_runs','run_detail','run_logs','run_cancel',
                   'ha_status','ha_request','runner_manifest','runtime_logs','usage']
        for name in modules:
            self.assertTrue((BASE/f'apps/deployments/runs/swagger/{name}.py').is_file())

    def test_trusted_runner_rejects_local_credentials(self):
        with patch.dict(os.environ, {'AUTOKOM_RUNNER_SHARED_KEY':'NEVER_LEAK','JENKINS_API_TOKEN':'HIDE_ME'},clear=False):
            with patch.object(agent.subprocess,'run') as execute:
                agent.execute(['true'])
                env = execute.call_args.kwargs['env']
                self.assertNotIn('AUTOKOM_RUNNER_SHARED_KEY',env)
                self.assertNotIn('JENKINS_API_TOKEN',env)
                self.assertIsInstance(execute.call_args.args[0],list)

    def test_image_tag_is_local_and_scoped_to_run(self):
        text=AGENT.read_text(encoding='utf-8')
        self.assertIn("autokom-local:'+rid",text)
        self.assertNotIn('AUTOKOM_IMAGE_REGISTRY',text)

    def test_no_kubernetes_or_registry_active(self):
        text=(CORE/'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        self.assertNotIn('kubectl ',text)
        self.assertNotIn('docker.sock',text)
        self.assertIn('autokom-isolated',text)

    def test_invalid_source_path_cannot_traverse_workspace(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            base=Path(td); (base/'.git').mkdir()
            with patch.dict(os.environ, {'WORKSPACE':str(base)}):
                with self.assertRaises(SystemExit):
                    agent.source({'build':{'root':'../outside'}})
                self.assertEqual(base,agent.source({'build':{'root':''}}))

    def test_sensitive_encryption_payload_not_written_to_logs(self):
        source=(BASE/'apps/deployments/runs/worker.py').read_text()
        tree=ast.parse(source)
        imports=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('SECRETS','BEARER') for t in n.targets)]
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='redact')
        mod=ast.Module(body=imports+[fn],type_ignores=[])
        env={'re':re}
        exec(compile(ast.fix_missing_locations(mod),'worker-test','exec'),env)
        self.assertNotIn('ghs_ABCDEF12345678901234',env['redact']('AUTOKOM_LOG: token=ghs_ABCDEF12345678901234'))
        self.assertIn('[REDACTED]',env['redact']('AUTOKOM_LOG: token=secret123'))

    def test_signed_manifest_time_bound_and_hmac(self):
        source=(BASE/'apps/deployments/runs/runner_manifest.py').read_text()
        tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='check_runner_signature')
        fake_time=SimpleNamespace(time=lambda:1234567890)
        class Denied(Exception): pass
        settings=SimpleNamespace(AUTOKOM_RUNNER_SHARED_KEY='x'*40)
        env={'hashlib':hashlib,'hmac':hmac,'re':re,'time':fake_time,
             'settings':settings,'PermissionDenied':Denied}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),
                     'auth-test','exec'),env)
        runid='12345678-aaaa-bbbb-cccc-000000000002'
        signature=hmac.new(settings.AUTOKOM_RUNNER_SHARED_KEY.encode(),f'autokom-runner:v1:1234567890:{runid}'.encode(),hashlib.sha256).hexdigest()
        env['check_runner_signature'](runid,'1234567890',signature)
        with self.assertRaises(Denied):
            env['check_runner_signature'](runid,'1234567890',signature[:-1]+'0' if signature[-1]!='0' else signature[:-1]+'1')
        with self.assertRaises(Denied):
            env['check_runner_signature'](runid,'1234567000',signature)

    def test_worker_never_calls_untrusted_shell(self):
        body=(BASE/'apps/deployments/runs/worker.py').read_text()
        self.assertNotIn('shell=True',body)
        self.assertNotIn('subprocess.',body)
        self.assertIn('JenkinsClient()',body)

    def test_webhook_verification_precedes_enqueue(self):
        body=(BASE/'apps/projects/git/views.py').read_text()
        self.assertLess(body.index('verify_signature(raw'),body.index('queue_verified_push(event)'))

    def test_migrations_versioned(self):
        self.assertTrue((BASE/'apps/deployments/runs/migrations/0003_deploymentlogentry.py').is_file())
        self.assertTrue((BASE/'apps/plans/entitlements/migrations/0002_highavailabilityoption.py').is_file())

if __name__=='__main__':
    unittest.main()
