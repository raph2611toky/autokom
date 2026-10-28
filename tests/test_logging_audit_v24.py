"""v24: read-only dashboard audit and consistent long-operation spinner."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import logging_audit as a
import logging_dashboards as d
import logging_stack as s
import logging_runtime as r
import logging_monitoring as m
import logs_canary as c
import autokom_cli as cli

class LoggingAuditV24(unittest.TestCase):
    def test_four_preserved_uids_and_raw_unfiltered_panel(self):
        ds=d.dashboards()
        self.assertEqual([x['uid'] for x in ds],['autokom-logs','autokom-logging-health','autokom-logging-security','autokom-logging-cicd'])
        self.assertEqual(ds[0]['panels'][0]['targets'][0]['expr'],'{platform="autokom"}')
        self.assertEqual(ds[0]['panels'][1]['type'],'logs')
        self.assertIn('$source', ds[0]['panels'][1]['targets'][0]['expr'])
        self.assertEqual(ds[1]['panels'][0]['datasource']['uid'],'autokom-prometheus')
    def test_loki_query_variables_use_structured_label_values(self):
        items=d.dashboards()[0]['templating']['list']
        self.assertEqual([x['name'] for x in items],['source','service_name','search'])
        for item in items[:2]:
            self.assertEqual(item['query']['type'],1)
            self.assertEqual(item['query']['label'],item['name'])
            self.assertEqual(item['query']['stream'],'{platform="autokom"}')
            self.assertTrue(item['multi'] and item['includeAll'])
    def test_dashboard_json_still_valid(self):
        for name,data in d.serialized().items():
            self.assertTrue(name.endswith('.json'))
            self.assertIn('autokom-managed',json.loads(data)['tags'])
    def test_literal_server_queries_and_grafana_templating(self):
        self.assertEqual(a.resolved_query('{platform="autokom",source=~"$source"}'), '{platform="autokom",source=~".*"}')
        self.assertEqual(a.resolved_query('{platform="autokom"} |~ "${search:regex}"'),'{platform="autokom"} |~ ""')
        with self.assertRaises(ValueError):a.resolved_query('${unknown}')
    def test_loki_series_payload_is_direct_list(self):
        self.assertEqual(a.stream_labels({'data':[{'platform':'autokom','source':'systemd'}]}),[{'platform':'autokom','source':'systemd'}])
        self.assertEqual(a.stream_labels({'data':{'result':[]}}),[])
    def test_metric_and_log_query_results(self):
        self.assertEqual(a.result_count({'data':{'result':[{'values':[[123,'foo'],[124,'bar']]}]}}),2)
    def test_audit_unavailable_is_nonmutating_and_exits_nonzero(self):
        with patch.object(a,'fetch',return_value=(None,'URLError')),patch.object(a,'read_dashboards',return_value=[]),patch.object(a,'LDS',Path('/nonexistent-v24-loki')),patch.object(a,'PDS',Path('/nonexistent-v24-prom')):
            buf=io.StringIO()
            with redirect_stdout(buf):status=a.run_audit()
        self.assertEqual(status,1)
        self.assertIn('Loki',buf.getvalue())
        self.assertNotIn('sudo systemctl',buf.getvalue())
    def test_cli_audit_is_read_only(self):
        self.assertEqual(cli.make_parser().parse_args(['logs','audit']).action,'audit')
        with patch.object(a,'run_audit',return_value=0) as func:
            self.assertEqual(s.main(['audit']),0)
        func.assert_called_once_with()
    def test_runtime_subprocess_uses_existing_activity(self):
        with patch('logging_runtime.subprocess.run',return_value=subprocess.CompletedProcess(['id'],0,'','')) as run,patch('tool_banner.Activity') as activity:
            r.cmd('id','-u','alloy')
        activity.assert_called_once()
        run.assert_called_once()
    def test_monitoring_subprocess_uses_existing_activity(self):
        with patch('logging_monitoring.subprocess.run',return_value=subprocess.CompletedProcess(['promtool'],0,'','')) as run,patch('tool_banner.Activity') as activity:
            m.command('promtool','--version')
        activity.assert_called_once()
        run.assert_called_once()
    def test_runtime_migration_modes_preserved(self):
        self.assertIn('127.0.0.1:3100',r.native_files()['config.alloy'])
        self.assertIn('http://loki:3100',s.alloy_config('loki'))

if __name__=='__main__': unittest.main()
