"""Non-network tests for the customer-visible Jenkins diagnostic stream."""
import importlib.util
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / 'apps/deployments/runs/console_stream.py'
spec = importlib.util.spec_from_file_location('autokom_console_diagnostics', FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TechnicalLogContractTests(unittest.TestCase):
    def test_pipeline_steps_and_errors_are_logged(self):
        source = ('[Pipeline] stage\nAUTOKOM_STAGE:source:RUNNING\n'
                  'ERROR: checkout failed\nAUTOKOM_STAGE:source:FAILED\n')
        rows, pending = module.consume_diagnostic_chunk('', source)
        self.assertEqual(pending, '')
        self.assertIn('AUTOKOM_TECH:[Pipeline] stage', rows)
        self.assertIn('AUTOKOM_STAGE:source:RUNNING', rows)
        self.assertIn('AUTOKOM_TECH:ERROR: checkout failed', rows)
    def test_technical_text_can_cross_two_http_chunks(self):
        first, pending = module.consume_diagnostic_chunk('', 'ERROR: cannot fetch')
        self.assertEqual(first, [])
        second, pending = module.consume_diagnostic_chunk(pending, ' repository\n')
        self.assertEqual(second, ['AUTOKOM_TECH:ERROR: cannot fetch repository'])
        self.assertEqual(pending, '')
    def test_sensitive_and_arbitrary_stdout_are_not_kept(self):
        text = ('ERROR: token=super-secret\n'
                'ERROR: ghp_abcdefgh1234567890\n'
                'ERROR: https://me:pass@github.com/repo\n'
                'ERROR: 9ae6a7f2d4a709ee5a65ee706f5076fa\n'
                '-----BEGIN PRIVATE KEY-----\n'
                'arbitrary secret stdout\n'
                '[Pipeline] stage\n')
        rows, pending = module.consume_diagnostic_chunk('', text)
        self.assertEqual(rows, ['AUTOKOM_TECH:[Pipeline] stage'])
    def test_known_missing_jenkins_credentials_produces_generic_tip(self):
        self.assertEqual(module.safe_technical_line(
            "ERROR: Could not find credentials entry with ID 'autokom-github-app'"),
            'Référence d’identifiants du job introuvable : intervention administrateur nécessaire.')
    def test_scoped_api_and_explicit_mode(self):
        views = (FILE.parent/'views.py').read_text()
        self.assertIn('accessible_run(request.user, project_id, environment_id, run_id)', views)
        self.assertIn("mode == 'simple'", views)
        self.assertIn("qs.exclude(level='DETAIL')", views)
        self.assertIn("mode not in ('technical', 'simple')", views)
    def test_worker_keeps_only_approved_stream_and_stage_is_scoped(self):
        worker = (FILE.parent/'worker.py').read_text()
        self.assertIn('consume_diagnostic_chunk', worker)
        self.assertIn("level='TECH'", worker)
        self.assertIn('select_for_update()', worker)
        self.assertIn('DeploymentLogEntry.objects.filter(deployment=current', worker)

if __name__=='__main__':
    unittest.main()
