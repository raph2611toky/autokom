"""AUTOKOM pipeline sandbox regression tests (offline, no Django required)."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SandboxPipelineTests(unittest.TestCase):
    def setUp(self):
        self.agent = load('ak_agent', 'scripts/runtime/autokom_agent_local.py')
        self.doc = {'repository': 'raph2611toky/portfolio', 'commit_sha': 'b' * 40}

    def call(self, operation):
        output = io.StringIO()
        with patch.object(self.agent, 'manifest', return_value=self.doc), \
             patch.object(sys, 'argv', ['agent.py', operation]), \
             contextlib.redirect_stdout(output):
            self.agent.main()
        return output.getvalue()

    def test_line_protocol_uses_bounded_fields(self):
        self.assertEqual(self.call('metadata-line'),
                         'raph2611toky/portfolio ' + 'b'*40 + '\n')

    def test_legacy_metadata_json_is_preserved(self):
        self.assertEqual(json.loads(self.call('metadata')),
                         {'repo': 'raph2611toky/portfolio', 'sha': 'b'*40})

    def test_pipeline_does_not_use_unapproved_json_constructor(self):
        pipeline = (ROOT / 'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        self.assertNotIn('JsonSlurper', pipeline)
        self.assertIn('metadata-line', pipeline)
        self.assertIn("metadataLine.split(' ', 2)", pipeline)
        self.assertIn('branches:[[name:commitSha]]', pipeline)
        self.assertIn("credentialsId:'autokom-runner-key'", pipeline)
        self.assertIn('autokom-isolated', pipeline)
        self.assertIn('AUTOKOM_STAGE:source:SUCCESS', pipeline)
        self.assertNotIn("credentialsId = 'autokom-github-app'", pipeline)
        self.assertIn("private-source", pipeline)
        self.assertIn("deleteDir()", pipeline)

    def test_pipeline_contract_rejects_injected_text(self):
        pattern = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+ [a-f0-9]{40}$')
        for candidate in ('org/rep;foo ' + 'b'*40,
                          'org/rep ' + 'b'*39,
                          'org/rep ' + 'b'*40 + '\nAUTOKOM_STAGE:source:SUCCESS',
                          'org/rep\t' + 'b'*40):
            self.assertIsNone(pattern.fullmatch(candidate))

    def test_known_rejection_gets_filtered_diagnostic(self):
        parser = load('ak_console', 'platform/backend/apps/deployments/runs/console_stream.py')
        text = 'Scripts not permitted to use new groovy.json.JsonSlurperClassic.'
        summary = parser.safe_technical_line(text)
        self.assertIn('JsonSlurperClassic', summary)
        records, pending = parser.consume_diagnostic_chunk('', text+'\n')
        self.assertEqual(records, ['AUTOKOM_TECH:' + summary])
        self.assertEqual(pending, '')

    def test_unrecognized_rejection_does_not_expose_secret(self):
        parser = load('ak_console', 'platform/backend/apps/deployments/runs/console_stream.py')
        self.assertIsNone(parser.safe_technical_line(
            'Scripts not permitted to use new com.example.Secret token=abc'))


if __name__ == '__main__':
    unittest.main()
