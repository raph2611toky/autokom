"""Fail-closed checks for AUTOKOM isolated BuildKit before a Jenkins client build."""
from contextlib import redirect_stdout
from io import StringIO
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autokom_runner_buildkit_test',
    ROOT / 'scripts/runtime/autokom_agent_local.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class BuildkitPreflightTests(unittest.TestCase):
    def failure(self, callback):
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            callback()
        self.assertEqual(stopped.exception.code, 2)
        message = output.getvalue()
        self.assertTrue(message.startswith('AUTOKOM_LOG: '))
        return message

    def test_missing_client_is_distinct(self):
        with patch.object(agent.shutil, 'which', return_value=None):
            text = self.failure(agent.buildkit_preflight)
        self.assertIn('client BuildKit absent', text)

    def test_missing_endpoint_is_distinct(self):
        with patch.object(agent.shutil, 'which', return_value='/usr/bin/buildctl'), \
             patch.dict(agent.os.environ, {}, clear=True):
            text = self.failure(agent.buildkit_preflight)
        self.assertIn('socket BuildKit isolé non configuré', text)

    def test_rootful_and_tcp_endpoints_refused(self):
        for endpoint in ('unix:///run/buildkit/buildkitd.sock',
                         'tcp://0.0.0.0:1234', 'unix:///tmp/buildkitd.sock'):
            with self.subTest(endpoint=endpoint), \
                 patch.object(agent.shutil, 'which', return_value='/usr/bin/buildctl'), \
                 patch.dict(agent.os.environ, {'BUILDKIT_HOST': endpoint}, clear=True):
                text = self.failure(agent.buildkit_preflight)
                self.assertIn('Compilation indisponible', text)

    def test_absent_local_socket_is_distinct(self):
        endpoint = 'unix:///run/autokom-buildkit/buildkitd.sock'
        with patch.object(agent.shutil, 'which', return_value='/usr/bin/buildctl'), \
             patch.dict(agent.os.environ, {'BUILDKIT_HOST': endpoint}, clear=True), \
             patch.object(agent.Path, 'is_symlink', return_value=False), \
             patch.object(agent.Path, 'stat', side_effect=FileNotFoundError):
            text = self.failure(agent.buildkit_preflight)
        self.assertIn('socket BuildKit absent', text)

    def test_run_command_return_code_is_explicit_without_output(self):
        with patch.object(agent.subprocess, 'run', side_effect=subprocess.CalledProcessError(17, ['buildctl'])), \
             patch.dict(agent.os.environ, {'AUTOKOM_RUNNER_SHARED_KEY': 'not-to-display'}):
            text = self.failure(lambda: agent.execute(['buildctl','build']))
        self.assertIn('code retour 17', text)
        self.assertNotIn('not-to-display', text)

    def test_timeout_is_distinct(self):
        with patch.object(agent.subprocess, 'run', side_effect=subprocess.TimeoutExpired('buildctl', 4)):
            text = self.failure(lambda: agent.execute(['buildctl','build'], timeout=4))
        self.assertIn('expirée', text)

    def test_other_tool_failure_is_not_described_as_buildkit(self):
        with patch.object(agent.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['semgrep'])):
            text = self.failure(lambda: agent.execute(['semgrep', 'scan']))
        self.assertIn('Analyse refusée', text)
        self.assertNotIn('BuildKit', text)


if __name__ == '__main__':
    unittest.main()
