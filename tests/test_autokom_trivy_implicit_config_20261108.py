"""Regression: Trivy's implicit config in root or customer repositories must not be loaded.

All tests use fakes; no network, systemd, chmod, or actual scanners are required.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from scripts.lib import autokom_runtime_integrity as integrity
from scripts.runtime import autokom_agent_local as runner


class TrivyConfigIsolationTests(unittest.TestCase):
    def test_diagnostic_disables_implicit_configuration_and_uses_builder_cwd(self):
        with TemporaryDirectory() as folder:
            cache = Path(folder) / 'private-cache'
            cache.mkdir()
            result = SimpleNamespace(returncode=0, stdout='Version: 0.76.2', stderr='')
            with patch.object(integrity.shutil, 'which', return_value='/usr/bin/trivy'), \
                 patch.object(integrity.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=991)), \
                 patch.object(integrity, 'TRIVY_CACHE', cache), \
                 patch.object(integrity, '_run', return_value=result) as command:
                probe = integrity._probe_builder_tool('trivy')
            self.assertTrue(probe.ok)
            arguments = command.call_args_list[0].args[0]
            self.assertIn('--config=', arguments)
            self.assertIn('--version', arguments)
            self.assertEqual(command.call_args_list[0].kwargs['cwd'], integrity.BUILDER_HOME)
            self.assertIn('TRIVY_CACHE_DIR=' + str(cache), arguments)

    def test_implicit_config_error_has_specific_reason(self):
        failure = SimpleNamespace(returncode=1, stdout='', stderr=(
            'config file "trivy.yaml" loading error: open trivy.yaml: permission denied'))
        with patch.object(integrity.shutil, 'which', return_value='/usr/bin/trivy'), \
             patch.object(integrity.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=991)), \
             patch.object(integrity, '_run', return_value=failure):
            probe = integrity._probe_builder_tool('trivy')
        self.assertEqual(probe.problem, 'implicit-config-inaccessible')

    def test_real_runner_rejects_implicit_client_repository_configuration(self):
        with patch.object(runner.subprocess, 'run') as execute:
            runner.execute(['trivy', 'fs', '--scanners', 'vuln,secret', '--severity',
                            'HIGH,CRITICAL', '/tmp/client-project'])
        args = execute.call_args.args[0]
        self.assertEqual(args[:5], ['trivy', 'fs', '--config=', '--ignorefile=', '--secret-config='])
        self.assertEqual(args[-1], '/tmp/client-project')
        self.assertEqual(args.count('--config='), 1)
        env = execute.call_args.kwargs['env']
        self.assertEqual(env['TRIVY_CACHE_DIR'], '/var/lib/autokom-jenkins-agent-v2/.cache/trivy')

    def test_version_invocation_uses_no_implicit_config(self):
        with patch.object(runner.subprocess, 'run') as execute:
            runner.execute(['trivy', '--version'])
        self.assertEqual(execute.call_args.args[0], ['trivy', '--config=', '--version'])

    def test_other_tools_not_altered(self):
        with patch.object(runner.subprocess, 'run') as execute:
            runner.execute(['semgrep', '--version'])
        self.assertEqual(execute.call_args.args[0], ['semgrep', '--version'])

    def test_frontend_pipeline_uses_trusted_scan_configuration(self):
        src = Path(integrity.ROOT / 'scripts/lib/autokom_frontend_manager.py')
        tree = ast.parse(src.read_text(encoding='utf8'))
        scans = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'builder_run' and node.args:
                if isinstance(node.args[0], ast.List):
                    try:
                        arr = ast.literal_eval(node.args[0])
                    except (ValueError, TypeError):
                        continue
                    if arr[:2] == ['trivy', 'fs']:
                        scans.append(arr)
        self.assertEqual(len(scans), 1)
        for flag in ('--config=', '--ignorefile=', '--secret-config='):
            self.assertIn(flag, scans[0])


if __name__ == '__main__':
    unittest.main()
