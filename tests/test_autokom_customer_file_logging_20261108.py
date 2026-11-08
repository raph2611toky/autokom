"""No Django dependency: regression contract for the per-run client-safe trace."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'platform/backend/apps/deployments/runs/worker.py'
VIEWS = ROOT / 'platform/backend/apps/deployments/runs/views.py'


class CustomerFileLoggingTests(unittest.TestCase):
    def test_trusted_extra_traces_without_summary_mutation(self):
        source = WORKER.read_text()
        parsed = ast.parse(source)
        functions = {fn.name: fn for fn in parsed.body if isinstance(fn, ast.FunctionDef)}
        self.assertIn('add_customer_trace', functions)
        self.assertIn("level='DETAIL'", source)
        self.assertIn('select_for_update()', source)
        self.assertIn('AUTOKOM_TECH:release:', source)
        self.assertGreaterEqual(source.count('add_customer_trace(run,'), 10)

    def test_export_still_checks_project_permissions(self):
        source=VIEWS.read_text()
        self.assertIn('customer_file_log_line(entry)',source)
        self.assertIn('accessible_run(request.user, project_id, environment_id, run_id)', source)
        self.assertIn("qs.exclude(level='DETAIL')",source)
        self.assertIn("response['Cache-Control'] = 'no-store, private'",source)

    def test_no_docker_or_jenkins_credentials_exported(self):
        source=WORKER.read_text()
        start=source.index('def add_customer_trace(')
        end=source.index('def add_log(',start)
        impl=source[start:end]
        self.assertNotIn("manifest['variables']",impl)
        self.assertNotIn('subprocess.run(',impl)

if __name__ == '__main__': unittest.main()
