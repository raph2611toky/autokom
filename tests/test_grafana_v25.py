"""Non-regression: Grafana visibility, official plugin discovery, safe operations."""
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import grafana_bridge as b
import grafana_plugins as g
import autokom_cli as cli
import logging_stack as logs
import cli_colors

class V25Tests(unittest.TestCase):
    def test_grafana_db_inspect_real_sqlite_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'grafana.db'
            con=sqlite3.connect(db)
            con.executescript('CREATE TABLE data_source(uid text,type text,url text);CREATE TABLE dashboard(uid text,data text);')
            con.execute('INSERT INTO data_source VALUES(?,?,?)',('autokom-loki','loki',b.LOKI))
            con.execute('INSERT INTO dashboard VALUES(?,?)',('autokom-logs',json.dumps({'panels':[{'targets':[{'expr':'{platform="autokom"}'}]}]})))
            con.commit();con.close()
            obj=b.local_state(db)
            self.assertTrue(obj['available'])
            self.assertEqual(obj['datasource']['type'],'loki')
            self.assertEqual(obj['dashboards']['autokom-logs']['panels'],1)
    def test_local_db_missing_is_not_wrongly_treated_as_absent_datasource(self):
        self.assertFalse(b.local_state(Path('/no-such-grafana-v25-db'))['available'])
    def test_drift_detection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'autokom-logs.json').write_text(json.dumps({'panels':[{'targets':[{'expr':'{}'}]}]}))
            state={'available':True,'datasource':{'uid':'autokom-loki','type':'loki','url':b.LOKI},'dashboards':{'autokom-logs':{'queries':['{platform="autokom"}']}}}
            self.assertEqual(len(b.drift(state,root)),1)
    def test_grafana_query_api_validates_through_grafana_not_direct_loki(self):
        def fake(path,token,**kw):
            if path=='/api/datasources/uid/autokom-loki':
                return {'type':'loki','url':b.LOKI},None
            self.assertEqual(path,'/api/ds/query')
            self.assertEqual(kw['method'],'POST')
            self.assertIn('platform',kw['body']['queries'][0]['expr'])
            return {'results':{'A':{'status':200,'frames':[{'data':{'values':[[123],["line"]]}}]}}},None
        with patch.object(b,'request',side_effect=fake):
            result=b.api_probe('fake-service-token',now=999999999999)
        self.assertTrue(result['ok']);self.assertEqual(result['samples'],1)
    def test_without_token_api_probe_never_calls_network(self):
        with patch.object(b,'request') as req:
            self.assertFalse(b.api_probe('')['checked'])
            req.assert_not_called()
    def test_plugins_are_real_only_with_official_ids(self):
        items=g.normalize({'plugins':[{'slug':'grafana-lokiexplore-app','name':'Logs Drilldown','type':'app','description':'Explore logs'}, {'id':'../evil','name':'Bad'}]})
        self.assertEqual([v['id'] for v in items],['grafana-lokiexplore-app'])
        self.assertEqual(g.rank(items,'logs drilldown','app')[0]['id'],'grafana-lokiexplore-app')
        self.assertFalse(g.rank(items,'nonsense'))
    def test_install_never_executes_in_check(self):
        with patch.object(g,'detail',return_value={'slug':'grafana-lokiexplore-app','name':'Logs Drilldown'}) as check,patch.object(g,'cli_command') as c:
            with redirect_stdout(io.StringIO()):self.assertEqual(g.install('grafana-lokiexplore-app'),0)
            check.assert_called_once();c.assert_not_called()
    def test_deprecated_renderer_never_installed(self):
        with patch.object(g,'detail',return_value={'slug':'grafana-image-renderer','name':'Renderer'}),patch.object(g,'cli_command') as cli_cmd:
            with self.assertRaises(g.PluginsError):g.install('grafana-image-renderer',apply=True)
            cli_cmd.assert_not_called()
    def test_filters_and_synonyms(self):
        self.assertTrue(g.renderer_intent('grafana to image'))
        self.assertFalse(g.renderer_intent('logs drilldown'))
        self.assertEqual(g.rank([{'id':'foo-panel','name':'foo','type':'panel','description':'foo','signature':'grafana','status':'stable'}],'foo','app'),[])
    def test_cli_help_and_aliases(self):
        tests=[['grafana','plugins','search','-h'],['grafana','plugins','install','-h'],
               ['tools','grafana','plugins','-h'],['logs','grafana','-h'],
               ['logs','runtime','systemd','-h'],['tools','grafana','files','-h']]
        for args in tests:
            with self.subTest(args=args):
                with redirect_stdout(io.StringIO()) as capture:
                    try:status=cli.main(args)
                    except SystemExit as exc:status=exc.code
                self.assertEqual(status,0)
                self.assertIn('usage:',capture.getvalue())
    def test_log_action_bridge(self):
        with patch.object(b,'reconcile',return_value=0) as call:
            self.assertEqual(logs.main(['grafana','--check']),0)
            call.assert_called_once_with(apply=False)
    def test_color_output_plain_and_tty(self):
        buf=io.StringIO()
        cli_colors.pretty_print('  ✓ test',file=buf)
        self.assertEqual(buf.getvalue(),'  ✓ test\n')
        buf.isatty=lambda:True
        with patch.dict(os.environ,{'TERM':'xterm-256color'},clear=False):
            cli_colors.pretty_print('  ✗ failure',file=buf)
            self.assertIn('\x1b[',buf.getvalue())
    def test_api_does_not_leak_tokens_in_error(self):
        self.assertEqual(b.api_probe('')['reason'],'AUTOKOM_GRAFANA_TOKEN non fourni')
    def test_malformed_catalog_rejected(self):
        with self.assertRaises(g.PluginsError):g.normalize({'unexpected':[]})

if __name__=='__main__':unittest.main()
