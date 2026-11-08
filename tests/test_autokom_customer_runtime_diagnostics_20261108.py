"""Regression: keep only safe per-run startup diagnostics before rollback."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_customer_diagnostics.py'
spec = importlib.util.spec_from_file_location('customer_diagnostics', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RuntimeDiagnosticsTest(unittest.TestCase):
    def test_secret_and_arbitrary_stdout_rejected(self):
        self.assertIsNone(module.safe_log_line('ERROR: token=super-secret'))
        self.assertIsNone(module.safe_log_line('password="Lady1234"'))
        self.assertIsNone(module.safe_log_line('customer email and data'))
        self.assertEqual(module.safe_log_line('ModuleNotFoundError: No module named \'flask\''),
                         'ModuleNotFoundError: No module named \'flask\'')
        self.assertEqual(module.safe_log_line('sqlite3.OperationalError: no such table: configurations'),
                         'sqlite3.OperationalError: no such table: configurations')

    def test_docker_stderr_is_included_for_startup_errors(self):
        fake=type('Completed',(), {'stdout':'', 'stderr':"ModuleNotFoundError: No module named 'flask'\n", 'returncode':0})()
        with patch.object(module.subprocess, 'run', return_value=fake):
            self.assertIn('ModuleNotFoundError', module._read_docker('autokom-id',['logs','--tail','90']))

    def test_capture_keeps_only_project_matching_bounded_lines(self):
        env='c'*8+'-cccc-4ccc-8ccc-'+'c'*12
        rid='d'*8+'-dddd-4ddd-8ddd-'+'d'*12
        state={'Status':'exited','ExitCode':3,'OOMKilled':False}
        log=('2026-11-08T19:04:00Z ModuleNotFoundError: No module named \'flask\'\n'
             '2026-11-08T19:04:00Z ERROR: token=super-secret\n'
             '2026-11-08T19:04:00Z stdout customer private text\n')
        with tempfile.TemporaryDirectory() as root, patch.object(module, '_read_docker', side_effect=[json.dumps(state),log]):
            result=module.capture(root,env,rid,'autokom-'+'d'*20)
            self.assertEqual(result['container_status'], 'exited')
            self.assertEqual(result['exit_code'],3)
            self.assertEqual(result['lines'], ["ModuleNotFoundError: No module named 'flask'"])
            self.assertEqual(module.read(root,env,rid)['lines'],result['lines'])
            self.assertFalse(module.read(root,'wrong-env',rid)['available'])
            filename=Path(root)/'diagnostics'/(rid+'.json')
            self.assertEqual(filename.stat().st_mode & 0o777, 0o600)

    def test_broker_captures_before_container_removal(self):
        broker=(MODULE.parent/'autokom_platform_broker.py').read_text()
        self.assertLess(broker.index('capture_customer_diagnostics(STATE'),
                        broker.index("subprocess.run(['docker','rm','-f',name]"))
        self.assertIn("if action=='runtime.diagnostics':",broker)

    def test_simple_is_old_technical_and_detailed_is_scoped(self):
        root=MODULE.parents[2]
        views=(root/'platform/backend/apps/deployments/runs/views.py').read_text()
        worker=(root/'platform/backend/apps/deployments/runs/worker.py').read_text()
        self.assertIn("qs.exclude(level='DETAIL')",views)
        self.assertIn('accessible_run(request.user, project_id, environment_id, run_id)',views)
        self.assertIn('AUTOKOM_TRACE:release:docker:',worker)
        self.assertIn('DeploymentLogDownloadAPIView',views)


if __name__=='__main__': unittest.main()
