"""Independent regression tests; no Django/MySQL installation is required.

Integration with Jenkins, MySQL and the AUTOKOM socket is deliberately out of
scope for these unit tests: run the read-only production checklist separately.
"""
import ast
import importlib.util
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1]
RUNS = BASE / 'apps/deployments/runs'
spec = importlib.util.spec_from_file_location('autokom_console_stream', RUNS / 'console_stream.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConsoleSyncTests(unittest.TestCase):
    def test_fragmented_marker_is_emitted_only_when_complete(self):
        items, tail = module.consume_chunk('', 'AUTOKOM_STAGE:build:SU')
        self.assertEqual(items, [])
        items, tail = module.consume_chunk(tail, 'CCESS\n')
        self.assertEqual(items, ['AUTOKOM_STAGE:build:SUCCESS'])
        self.assertEqual(tail, '')

    def test_terminal_flushes_pending_marker(self):
        items, tail = module.consume_chunk('AUTOKOM_LOG: Build terminé.', '', final=True)
        self.assertEqual(items, ['AUTOKOM_LOG: Build terminé.'])
        self.assertEqual(tail, '')

    def test_more_than_250_lines_do_not_hide_last_stage(self):
        source = 'information client quelconque\n' * 320 + 'AUTOKOM_STAGE:build:SUCCESS\n'
        items, tail = module.consume_chunk('', source)
        self.assertEqual(items, ['AUTOKOM_STAGE:build:SUCCESS'])
        self.assertEqual(tail, '')

    def test_timestamp_prefix_is_supported(self):
        self.assertEqual(module.parse_marker('12:43:15  AUTOKOM_STAGE:sast:SUCCESS'),
                         'AUTOKOM_STAGE:sast:SUCCESS')
        self.assertEqual(module.parse_marker('[2026-11-04T12:43:15Z] AUTOKOM_LOG: OK'),
                         'AUTOKOM_LOG: OK')

    def test_do_not_accept_marker_embedded_in_customer_line(self):
        self.assertIsNone(module.parse_marker('payload: AUTOKOM_STAGE:sca:SUCCESS'))
        self.assertIsNone(module.parse_marker('AUTOKOM_STAGE:sca:SUCCESS  extra output'))

    def test_console_line_cap_enforced(self):
        with self.assertRaises(ValueError):
            module.consume_chunk('', 'z' * 70000)

    def test_nonterminal_pending_survives(self):
        items, tail = module.consume_chunk('AUTOKOM_LO', 'G: waiting', final=False)
        self.assertEqual(items, [])
        self.assertEqual(tail, 'AUTOKOM_LOG: waiting')


class WorkerStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (RUNS / 'worker.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.text)

    def test_django_worker_never_executes_shell(self):
        for unsafe in ('subprocess.', 'os.system(', 'shell=True', 'docker.sock'):
            self.assertNotIn(unsafe, self.text)

    def test_release_is_not_registered_before_docker_commit(self):
        poll = next(node for node in self.tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'poll_one')
        commit_line = min(node.lineno for node in ast.walk(poll)
                          if isinstance(node, ast.Call) and node.args
                          and isinstance(node.args[0], ast.Constant)
                          and node.args[0].value == 'runtime.commit')
        db_line = min(node.lineno for node in ast.walk(poll)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == 'register_release')
        self.assertLess(commit_line, db_line)

    def test_error_after_commit_blocks_reconciliation_instead_of_rollback(self):
        self.assertIn('Docker publié, état MySQL à réconcilier.', self.text)
        self.assertIn("status = 'BLOCKED'", self.text)
        self.assertIn("request_control('runtime.rollback'", self.text)

    def test_console_processing_is_transactional(self):
        self.assertIn('def ingest_console(', self.text)
        self.assertIn('DeploymentRun.objects.select_for_update()', self.text)
        self.assertIn("current.save(update_fields=['console_offset', 'console_pending'])", self.text)

    def test_jenkins_failure_does_not_terminate_poll(self):
        self.assertIn('AutokomControlUnavailable', self.text)
        self.assertIn('except (JenkinsUnavailable, AutokomControlUnavailable, ValueError)', self.text)

    def test_failure_is_exposed_as_generic_api_field(self):
        text = (RUNS / 'serializers.py').read_text(encoding='utf-8')
        self.assertIn("'error_message'", text)

    def test_idle_worker_does_not_call_jenkins(self):
        self.assertIn("if not DeploymentRun.objects.filter(status__in=['QUEUED', 'RUNNING']).exists():", self.text)


if __name__ == '__main__':
    unittest.main()
