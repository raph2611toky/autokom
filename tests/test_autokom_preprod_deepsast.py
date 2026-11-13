import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts' / part) for part in ('lib', 'runtime')]
import autokom_deep_sast as deep
import autokom_sast_report as report
import autokom_private_update as updater
import autokom_platform_broker as broker
import autokom_agent_local as runner


class DeepSASTTests(unittest.TestCase):
    def test_paths_and_bounds(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / 'source'
            root.mkdir()
            (root / 'sample.py').write_text('print("demo")\n')
            (root / '.hidden.py').write_text('not public')
            outside = Path(work) / 'outside.py'
            outside.write_text('not in checkout')
            (root / 'link.py').symlink_to(outside)
            def loc(path):
                return {'path': path, 'start': {'line': 1}, 'end': {'line': 1}}
            items = [loc('link.py'), loc('.hidden.py'), loc('../outside.py'), loc('sample.py')]
            self.assertEqual(list(deep.snippets(root, items)), ['print("demo")'])

    def test_lfs_pointer_is_not_a_model(self):
        with tempfile.TemporaryDirectory() as work:
            model = Path(work)
            for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors'):
                (model / name).write_text('version https://git-lfs.github.com/spec/v1')
            with self.assertRaises(ValueError):
                deep.model_files(model)

    def test_devops_never_calls_scanners(self):
        with patch.object(runner, 'source', side_effect=AssertionError('source resolved')), patch.object(runner, 'stage') as stage:
            runner.scan({'mode': 'DEVOPS'}, 'sast')
            stage.assert_called_once_with('sast', 'SKIPPED')

    def test_semgrep_report_only_counts_and_remains_blocking(self):
        def call(args, **kwargs):
            path = Path(args[args.index('--output') + 1])
            path.write_text(json.dumps({'results': [{'check_id': 'rule.safe', 'path': '/private/file.py',
                                                      'extra': {'lines': 'not-for-export'}}]}))
            return SimpleNamespace(returncode=1)
        result, code = report.collect(Path('/tmp'), '/trusted/rules.yml', {}, None, None, run=call)
        self.assertEqual(code, 1)
        self.assertEqual(result, {'count': 1, 'rules': {'rule.safe': 1},
                                  'ai': {'status': 'DISABLED', 'analyzed': 0}})
        self.assertNotIn('not-for-export', json.dumps(result))


class IsolationTests(unittest.TestCase):
    def test_nested_prefix_and_unrelated_hostname(self):
        self.assertTrue(broker._managed_client_host('pre.myportfolio.auto.ikom.mg', 'auto.ikom.mg'))
        self.assertFalse(broker._managed_client_host('dev.myportfolio.auto.ikom.mg', 'auto.ikom.mg'))
        self.assertFalse(broker._managed_client_host('pre.myportfolio.ikom.mg', 'auto.ikom.mg'))

    def test_wrong_update_checkout_is_rejected(self):
        with patch.object(updater, '_repository', return_value='master'):
            with tempfile.TemporaryDirectory() as work:
                for preprod in (False, True):
                    with self.assertRaises(updater.UpdateError):
                        updater.update(Path(work), check=True, preprod=preprod)

    def test_broker_denies_non_owned_dns_and_mig_in_staging(self):
        with patch.object(broker, 'PREPROD', True), patch.object(broker, '_runtime_dns_env', return_value={
            'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg',
            'AUTOKOM_PREPROD_ALLOWED_HOSTS': 'demo.auto.ikom.mg,pre.demo.auto.ikom.mg',
        }):
            for host in ('pre.auto.ikom.mg', 'production.auto.ikom.mg', 'external.example.com'):
                with self.assertRaises(broker.BrokerError):
                    broker.handle('domain.dns.create', {'hostname': host})
            self.assertFalse(broker.handle('ha.application.status', {})['application']['public_https_ready'])

    def test_jenkins_queue_is_scoped(self):
        reply = json.dumps({'task': {'name': 'autokom-client-deploy'}}).encode()
        with patch.object(broker, 'PREPROD', True), patch.object(broker, 'jenkins_config', return_value=(
            'https://jenkins.example.com', '/job/autokom-preprod-client-deploy', 'stage', 'test',
        )), patch.object(broker, 'jenkins_http', return_value=(200, {}, reply)) as request:
            with self.assertRaises(broker.BrokerError):
                broker.jenkins_action('jenkins.cancel', {'external_id': 'Q:42'})
            self.assertEqual(request.call_args.args[0], 'GET')
            self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
