"""AUTOKOM dual logs: static security rules and CLI UX without Docker daemon."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import logging_stack as logs
import autokom_cli as cli

class DualLogging(unittest.TestCase):
    def test_both_targets_are_fanned_out_from_one_alloy(self):
        config=logs.alloy_config('both')
        self.assertIn('loki.write.autokom.receiver',config)
        self.assertIn('otelcol.receiver.loki.opensearch.receiver',config)
        self.assertIn('discovery.docker',config)
        self.assertIn('loki.source.journal',config)
        self.assertIn('otelcol.exporter.otlp "opensearch"',config)
        self.assertIn('regex = "true"',config) # avoids ingestion loops

    def test_single_backend_configuration_has_no_unreachable_pipeline(self):
        for mode in ('loki','opensearch'):
            config=logs.alloy_config(mode)
            self.assertEqual('loki.write.autokom.receiver' in config,mode=='loki')
            self.assertEqual('otelcol.receiver.loki.opensearch.receiver' in config,mode=='opensearch')

    def test_ports_never_expose_on_wan(self):
        data=logs.compose('both')
        for port in (3100,9200,5601):
            self.assertIn(f'127.0.0.1:{port}:{port}',data)
        self.assertNotIn('\"0.0.0.0:3100:',data)
        self.assertNotIn('\"0.0.0.0:12345:',data)
        self.assertNotIn('\"0.0.0.0:9200:',data)
        self.assertIn('internal: true',data)
        self.assertIn('profiles: [opensearch]',data)

    def test_retention_policy_has_template_and_delete(self):
        self.assertIn('168h',logs.yaml_loki())
        p=logs.opensearch_ism_policy()['policy']
        self.assertEqual(p['ism_template'][0]['index_patterns'],['autokom-logs-*'])
        self.assertEqual(p['states'][0]['transitions'][0]['conditions']['min_index_age'],'14d')

    def test_existing_custom_file_refused(self):
        with TemporaryDirectory() as td,patch.object(logs,'STATE_DIR',Path(td)):
            (Path(td)/'compose.yaml').write_text('unmanaged')
            with self.assertRaises(logs.LogError):logs.install_files('both')

    def test_generated_files_idempotent_and_private(self):
        with TemporaryDirectory() as td,patch.object(logs,'STATE_DIR',Path(td)):
            pw=logs.install_files('both')
            self.assertEqual(pw,logs.install_files('both'))
            self.assertEqual((Path(td)/'.env').stat().st_mode & 0o777,0o600)
            self.assertEqual((Path(td)/'pipelines.yaml').stat().st_mode & 0o777,0o600)
            self.assertNotIn(pw,logs.compose('both'))
            self.assertIn('OPENSEARCH_INITIAL_ADMIN_PASSWORD', (Path(td)/'.env').read_text())
            (Path(td)/'config.alloy').write_text('manually edited')
            with self.assertRaises(logs.LogError):logs.install_files('both')

    def test_install_help_and_aliases(self):
        parser=cli.make_parser()
        self.assertEqual(cli.selected(parser.parse_args(['install','--only','pipeline'])),('services','plugins'))
        self.assertEqual(cli.selected(parser.parse_args(['install','--only','logging'])),('logs',))
        self.assertEqual(cli.selected(parser.parse_args(['install','--only','metrics'])),('observability',))
        self.assertEqual(cli.selected(parser.parse_args(['install','--from','logs'])),('logs',))
        with redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit): parser.parse_args(['install','-h'])
        self.assertIn('logs | logging',output.getvalue())
        self.assertIn('pipeline | cicd',output.getvalue())

    def test_install_check_does_not_touch_state(self):
        with TemporaryDirectory() as td,patch.object(logs,'STATE_DIR',Path(td)):
            with redirect_stdout(io.StringIO()):self.assertEqual(logs.main(['--check','--backend','both']),0)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_backend_preference_not_install_toggle(self):
        with TemporaryDirectory() as td, patch.object(logs,'PREFERENCE',Path(td)/'pref.json'), \
                patch.object(logs,'ready',return_value=True):
            with redirect_stdout(io.StringIO()):logs.use_backend('opensearch')
            self.assertEqual(logs.backend_state(),'opensearch')
            with redirect_stdout(io.StringIO()):logs.use_backend('loki')
            self.assertEqual(logs.backend_state(),'loki')

if __name__=='__main__':unittest.main()
