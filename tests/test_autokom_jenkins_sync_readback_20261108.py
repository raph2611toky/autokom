"""Readback/normalization regression tests for multi-account AUTOKOM Jenkins pipeline."""
from __future__ import annotations
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_runtime_admin as runtime
import autokom_jenkins_credentials as creds
import autokom_jenkins_pipeline as pipeline

ENV={
    'AUTOKOM_JENKINS_URL':'http://127.0.0.1:8080',
    'AUTOKOM_JENKINS_JOB':'autokom-client-deploy',
    'AUTOKOM_JENKINS_USER':'AdminJenkins',
    'AUTOKOM_JENKINS_API_TOKEN':'TEST-ONLY-NOT-REAL',
}
SOURCE = (ROOT/'scripts/runtime/Jenkinsfile.autokom-local').read_text(encoding='utf-8')

class FakeJenkins:
    def __init__(self, initial, *, retain_write=True, status=200):
        self.script=initial
        self.retain_write=retain_write
        self.status=status
        self.writes=0
    def __call__(self, env, method, path, data=None, content_type='application/xml'):
        assert env == ENV
        if method == 'GET' and path.endswith('/config.xml'):
            return 200,runtime.pipeline_xml(self.script)
        if method == 'POST' and path.endswith('/config.xml'):
            self.writes += 1
            if self.retain_write:
                self.script=ET.fromstring(data).findtext('./definition/script')
            return self.status,b''
        raise AssertionError(f'Unexpected request {method} {path}')

class JenkinsReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source_file=Path(self.temp.name)/'Jenkinsfile.autokom-local'
        self.source_file.write_text(SOURCE,encoding='utf-8')
        self.backup=Path(self.temp.name)/'backups'
    def run_sync(self, fake, apply):
        with patch.object(runtime,'load_env',return_value=ENV), \
             patch.object(runtime,'JOB_SCRIPT',self.source_file), \
             patch.object(runtime,'BACKUP',self.backup), \
             patch.object(runtime,'jenkins_request',side_effect=fake), \
             patch.object(runtime,'confirm'), \
             patch.object(runtime,'isolated_agent_status',return_value=(True,'online')), \
             patch.object(runtime,'jenkins_view_sync'), \
             patch.object(pipeline,'reconcile',return_value=SimpleNamespace(ready=True)), \
             patch.object(creds,'provision',return_value='managed'), \
             patch.object(runtime.time,'sleep'):
            out=io.StringIO()
            with redirect_stdout(out):
                result=runtime.jenkins_sync(apply=apply)
            return result,out.getvalue()

    def test_existing_job_with_xml_line_endings_is_idempotent(self):
        fake=FakeJenkins(SOURCE.replace('\n','\r\n').rstrip('\r\n'))
        code,out=self.run_sync(fake,apply=True)
        self.assertEqual(code,0)
        self.assertEqual(fake.writes,0)
        self.assertIn('déjà synchronisé',out)

    def test_writing_new_script_must_be_read_back_before_success(self):
        fake=FakeJenkins('pipeline { /* ancien script */ }')
        code,out=self.run_sync(fake,apply=True)
        self.assertEqual(code,0)
        self.assertEqual(fake.writes,1)
        self.assertIn('relu via l’API',out)
        self.assertFalse(runtime._pipeline_mismatch(SOURCE,fake.script))
        self.assertEqual(len(list(self.backup.glob('*.xml'))),1)

    def test_http_redirect_does_not_imply_success_without_readback(self):
        fake=FakeJenkins('pipeline { /* obsolète */ }',retain_write=False,status=302)
        with self.assertRaisesRegex(runtime.SetupError,'n’a pas conservé'):
            self.run_sync(fake,apply=True)
        self.assertEqual(fake.writes,1)

    def test_readback_rejects_failed_write_even_with_http_200(self):
        fake=FakeJenkins('pipeline { /* obsolète */ }',retain_write=False,status=200)
        with self.assertRaisesRegex(runtime.SetupError,'n’a pas conservé'):
            self.run_sync(fake,apply=True)

    def test_same_script_but_real_changes_are_not_ignored(self):
        self.assertTrue(runtime._pipeline_mismatch(SOURCE,SOURCE.replace('private-source','checkout-old')))
        self.assertEqual(runtime._pipeline_mismatch(SOURCE,SOURCE.replace('\n','\r\n')+'\r\n'),'')

    def test_invalid_pipeline_definition_rejected(self):
        bad_xml=b'<flow-definition><definition class="unexpected"><script>pipeline {}</script></definition></flow-definition>'
        with patch.object(runtime,'jenkins_request',return_value=(200,bad_xml)):
            with self.assertRaisesRegex(runtime.SetupError,'définition Pipeline'):
                runtime._read_jenkins_job_pipeline(ENV,'autokom-client-deploy')

    def test_fingerprints_do_not_expose_script_or_secrets(self):
        text='pipeline { TOKEN=PRIVATE_EXAMPLE_SECRET }'
        fingerprint=runtime._pipeline_fingerprint(text)
        self.assertIn('sha256=',fingerprint)
        self.assertNotIn('PRIVATE_EXAMPLE_SECRET',fingerprint)

    def test_private_tenant_checkout_still_has_no_global_github_credential(self):
        self.assertIn('private-source', SOURCE)
        self.assertNotIn('autokom-github-app', SOURCE)
        self.assertIn("credentialsId:'autokom-runner-key'", SOURCE)

if __name__=='__main__': unittest.main()
