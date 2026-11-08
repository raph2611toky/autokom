"""Regression: Jenkins must save the exact approved Unicode Groovy script.

Some Jenkins controllers decode the POST /config.xml body as Latin-1. AUTOKOM
must not rely on a UTF-8 XML declaration alone to preserve non-ASCII strings.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import autokom_runtime_admin as runtime
import autokom_jenkins_credentials as creds
import autokom_jenkins_pipeline as pipeline

ENV = {
    'AUTOKOM_JENKINS_URL': 'http://127.0.0.1:8080',
    'AUTOKOM_JENKINS_JOB': 'autokom-client-deploy',
    'AUTOKOM_JENKINS_USER': 'AdminJenkins',
    'AUTOKOM_JENKINS_API_TOKEN': 'TEST-NOT-A-REAL-TOKEN',
}


class Latin1Jenkins:
    """Emulates a Jenkins servlet that treats incoming XML bytes as Latin-1."""
    def __init__(self, script):
        self.script = script
        self.writes = 0

    def __call__(self, env, method, path, data=None, content_type='application/xml'):
        if method == 'GET' and path.endswith('/config.xml'):
            return 200, runtime.pipeline_xml(self.script)
        if method == 'POST' and path.endswith('/config.xml'):
            self.writes += 1
            # Simulate the problematic server-side request charset handling.
            decoded_by_servlet = data.decode('iso-8859-1')
            self.script = ET.fromstring(decoded_by_servlet).findtext('./definition/script')
            return 200, b''
        raise AssertionError(f'Unexpected Jenkins operation: {method} {path}')


class JenkinsUnicodeXmlTests(unittest.TestCase):
    def test_pipeline_request_uses_only_ascii_bytes_and_preserves_unicode(self):
        original = 'pipeline { echo "Exécution réussie — dépôt privé ✓ 👍 éèàç ’ & < >" }'
        xml = runtime.pipeline_xml(original)
        self.assertTrue(xml.isascii())
        self.assertIn(b'encoding=\'us-ascii\'', xml)
        self.assertIn(b'&#', xml)
        self.assertEqual(ET.fromstring(xml).findtext('./definition/script'), original)
        self.assertEqual(ET.fromstring(xml.decode('latin-1')).findtext('./definition/script'), original)

    def test_view_request_is_safe_for_french_description(self):
        xml = runtime.jenkins_view_xml('autokom-client-deploy')
        self.assertTrue(xml.isascii())
        self.assertIn('Exécutions clientes AUTOKOM', ET.fromstring(xml).findtext('description'))

    def test_actual_approved_private_source_pipeline_roundtrips(self):
        source = runtime.JOB_SCRIPT.read_text(encoding='utf-8')
        self.assertIn('private-source', source)
        self.assertNotIn('autokom-github-app', source)
        xml = runtime.pipeline_xml(source)
        self.assertTrue(xml.isascii())
        script = ET.fromstring(xml.decode('iso-8859-1')).findtext('./definition/script')
        self.assertEqual(script, source)
        self.assertEqual(runtime._pipeline_mismatch(source, script), '')

    def test_regression_reproduces_original_utf8_latin1_failure(self):
        source = 'pipeline { echo "Exécution réussie" }'
        root = ET.Element('flow-definition')
        ET.SubElement(ET.SubElement(root, 'definition'), 'script').text = source
        old_payload = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        self.assertNotEqual(ET.fromstring(old_payload.decode('latin-1')).findtext('./definition/script'), source)
        new_payload = runtime.pipeline_xml(source)
        self.assertEqual(ET.fromstring(new_payload.decode('latin-1')).findtext('./definition/script'), source)

    def test_apply_repairs_mojibake_and_second_apply_is_idempotent(self):
        source = runtime.JOB_SCRIPT.read_text(encoding='utf-8')
        old = source.encode('utf-8').decode('iso-8859-1')
        self.assertTrue(runtime._pipeline_mismatch(source, old))
        fake = Latin1Jenkins(old)
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / 'Jenkinsfile.autokom-local'
            file.write_text(source, encoding='utf-8')
            with patch.object(runtime, 'JOB_SCRIPT', file), \
                 patch.object(runtime, 'BACKUP', Path(td) / 'backup'), \
                 patch.object(runtime, 'load_env', return_value=ENV), \
                 patch.object(runtime, 'jenkins_request', side_effect=fake), \
                 patch.object(runtime, 'confirm'), \
                 patch.object(runtime, 'isolated_agent_status', return_value=(True, 'online')), \
                 patch.object(runtime, 'jenkins_view_sync'), \
                 patch.object(pipeline, 'reconcile', return_value=SimpleNamespace(ready=True)), \
                 patch.object(creds, 'provision', return_value='managed'), \
                 patch.object(runtime.time, 'sleep'):
                with redirect_stdout(io.StringIO()) as stream:
                    self.assertEqual(runtime.jenkins_sync(apply=True), 0)
                self.assertIn('relu via l’API', stream.getvalue())
                self.assertEqual(fake.writes, 1)
                self.assertEqual(fake.script, source)
                self.assertEqual(runtime._pipeline_mismatch(source, fake.script), '')
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(runtime.jenkins_sync(apply=True), 0)
                self.assertEqual(fake.writes, 1)

    def test_mismatch_still_blocks_unrelated_job_changes(self):
        source = 'pipeline { echo "sécurisé" }'
        altered = 'pipeline { echo "autre" }'
        self.assertNotEqual(runtime._pipeline_mismatch(source, altered), '')


if __name__ == '__main__':
    unittest.main()
