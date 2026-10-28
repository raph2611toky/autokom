"""v21 logging observability non-regression and failure safety tests (offline)."""
import json
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import logging_stack as ls
import logging_monitoring as lm
import logging_dashboards as ld
import logs_canary as canary
import autokom_cli
import yaml

class LoggingObservability21(unittest.TestCase):
    def test_sanitization_and_single_pipeline(self):
        for backend in ('loki','opensearch','both'):
            c=ls.alloy_config(backend)
            self.assertEqual(c.count('loki.process "autokom_sanitize"'),1)
            self.assertEqual(c.count('loki.process.autokom_sanitize.receiver'),3)
            self.assertIn('service_name',c)
            self.assertIn('stage.replace',c)
            self.assertIn('stage.structured_metadata',c)
            self.assertIn('\\\\s',c) # escape River string regex
            self.assertNotIn('request_id = "request_id"}\n  }\n  stage.labels',c)
            if backend in ('loki','both'):
                self.assertIn('loki.write.autokom.receiver',c)
            if backend in ('opensearch','both'):
                self.assertIn('otelcol.receiver.loki.opensearch.receiver',c)
    def test_alloy_metrics_private(self):
        c=yaml.safe_load(ls.compose('both'))
        a=c['services']['alloy']
        self.assertEqual(a['ports'],['127.0.0.1:12345:12345'])
        self.assertIn('--server.http.listen-addr=0.0.0.0:12345',a['command'])
        self.assertTrue(c['networks']['logging']['internal'])
        self.assertEqual(c['services']['loki']['ports'],['127.0.0.1:3100:3100'])
    def test_prometheus_idempotent_merge_preserves_existing(self):
        source='global:\n  scrape_interval: 30s\nscrape_configs:\n- job_name: jenkins\n  static_configs:\n  - targets: [127.0.0.1:8080]\nrule_files: [/etc/prometheus/alert-rules.yml]\n'
        changed=lm.merge_prometheus(source)
        actual=yaml.safe_load(changed)
        self.assertEqual(len(actual['scrape_configs']),4)
        self.assertEqual(actual['scrape_configs'][0]['job_name'],'jenkins')
        self.assertIn('/etc/prometheus/alert-rules.yml',actual['rule_files'])
        self.assertEqual(yaml.safe_load(lm.merge_prometheus(changed)),actual)
        self.assertIn(str(lm.RULES),actual['rule_files'])
        self.assertIn('autokom-logs-canary',set(j['job_name'] for j in actual['scrape_configs']))
    def test_reject_conflicting_job(self):
        src='scrape_configs:\n- job_name: autokom-alloy\n  static_configs:\n  - targets: [192.168.1.1:9999]\n'
        with self.assertRaises(lm.MonitorError):lm.merge_prometheus(src)
    def test_rule_alerts_namespaced(self):
        o=yaml.safe_load(lm.rules_yaml())
        group=o['groups'][0]
        names={a['alert'] for a in group['rules']}
        self.assertEqual(len(names),7)
        self.assertIn('AUTOKOMLogsEndToEndFailed',names)
        self.assertIn('AUTOKOMLogsCanaryStale',names)
        self.assertIn('AUTOKOMAlloyUnavailable',names)
    def test_four_grafana_dashboards_local_and_unique(self):
        ds=ld.dashboards()
        self.assertEqual(len(ds),4)
        self.assertEqual(len({d['uid'] for d in ds}),4)
        self.assertEqual(ds[0]['uid'],'autokom-logs')
        for d in ds:
            self.assertIn('autokom-managed',d['tags'])
            self.assertTrue(d['panels'])
            for panel in d['panels']:
                self.assertIn('targets',panel)
                self.assertIn('datasource',panel)
        for s in ld.serialized().values():json.loads(s)
    def test_canary_metrics_empty_and_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'probe.json'
            self.assertIn('autokom_logs_canary_success 0',canary.metrics(path))
            canary.atomic_result({'schema':1,'success':1,'timestamp':123,'duration_seconds':1.5,'state':'verified'},path)
            self.assertIn('autokom_logs_canary_success 1',canary.metrics(path))
            self.assertIn('autokom_logs_canary_duration_seconds 1.5',canary.metrics(path))
    def test_canary_query_contains_source_and_fresh_timestamp(self):
        example={'status':'success','data':{'result':[{'values':[['1234','AUTOKOM_CANARY_TEST']]}]}}
        with patch.object(canary,'loki_api',return_value=example) as mocked:
            self.assertTrue(canary.seen('AUTOKOM_CANARY_TEST',1000))
        self.assertIn('source="systemd"',mocked.call_args.args[1]['query'])
    def test_canary_failure_is_visible_not_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(canary,'STATE',Path(tmp)/'canary.json'), \
             patch.object(canary,'emit',side_effect=RuntimeError('read failed')):
            with redirect_stdout(io.StringIO()):self.assertEqual(canary.probe(5),1)
            self.assertIn('autokom_logs_canary_success 0',canary.metrics(Path(tmp)/'canary.json'))
    def test_systemd_uses_local_source_and_is_private(self):
        files=lm.systemd_files()
        self.assertEqual(len(files),3)
        self.assertIn('127.0.0.1',Path(lm.CANARY_SCRIPT).read_text())
        self.assertIn('OnUnitActiveSec=5min',files[lm.CANARY_TIMER])
        self.assertIn('User=nobody',files[lm.EXPORTER_SERVICE])
    def test_monitoring_refuses_manual_edits_to_owned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit=Path(tmp)/'unit.service'
            self.assertTrue(lm.managed_write(unit,lm.MARKER+'[Unit]\nDescription=OK\n'))
            self.assertFalse(lm.managed_write(unit,lm.MARKER+'[Unit]\nDescription=OK\n'))
            unit.write_text(lm.MARKER+'[Unit]\nDescription=Operator custom\n')
            with self.assertRaises(lm.MonitorError):
                lm.managed_write(unit,lm.MARKER+'[Unit]\nDescription=OK\n')

    def test_validate_alloy_checks_image_before_changes(self):
        import subprocess
        with patch.object(ls,'run',return_value=subprocess.CompletedProcess([],0,'','')) as mock:
            ls.validate_alloy('loki')
        cmd=mock.call_args.args[0]
        self.assertIn('validate',cmd)
        self.assertIn('--network',cmd)
        self.assertIn('none',cmd)
        self.assertIn(ls.IMAGES['alloy'],cmd)
        with patch.object(ls,'run',return_value=subprocess.CompletedProcess([],2,'','bad syntax')):
            with self.assertRaises(ls.LogError):ls.validate_alloy('loki')

    def test_cli_supports_reconcile_and_verify(self):
        parser=autokom_cli.make_parser()
        self.assertEqual(parser.parse_args(['logs','reconcile','--apply']).action,'reconcile')
        self.assertEqual(parser.parse_args(['logs','verify']).action,'verify')
        self.assertEqual(parser.parse_args(['install','logs']).cmd,'install')
    def test_installed_backend_never_guesses(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(ls,'STATE_DIR',Path(tmp)):
            with self.assertRaises(ls.LogError):ls.installed_backend()
            (Path(tmp)/'config.alloy').write_text(ls.alloy_config('loki'))
            self.assertEqual(ls.installed_backend(),'loki')
            (Path(tmp)/'config.alloy').write_text(ls.alloy_config('both'))
            self.assertEqual(ls.installed_backend(),'both')

if __name__=='__main__':unittest.main()
