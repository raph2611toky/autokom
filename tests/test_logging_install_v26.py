"""v26: install-time Grafana integration and Docker/systemd parity checks."""
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import logging_stack as ls
import logging_dashboards as ld
import grafana_bridge as bridge
import cli_colors as colors

class LoggingInstallV26(unittest.TestCase):
    def test_provider_matches_existing_observability_contract(self):
        import yaml
        from observability_dashboards import PROVIDER, DIRECTORY
        conf=yaml.safe_load(ls.provider_content())
        self.assertEqual(ls.GRAFANA_PROVIDER,PROVIDER)
        self.assertEqual(ls.GRAFANA_DASH_DIRECTORY,DIRECTORY)
        self.assertEqual(conf['providers'][0]['options']['path'],str(DIRECTORY))
        self.assertEqual(conf['providers'][0]['name'],'autokom')

    def test_provider_created_once_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'autokom.yaml'
            with patch.object(ls,'GRAFANA_PROVIDER',path):
                self.assertTrue(ls.ensure_provider())
                self.assertFalse(ls.ensure_provider())
                self.assertTrue(ls.verify_provider())
                self.assertEqual(path.read_text(),ls.provider_content())

    def test_existing_operator_provider_conflict_refused_without_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'autokom.yaml'
            file.write_text('apiVersion: 1\nproviders: []\n')
            with patch.object(ls,'GRAFANA_PROVIDER',file):
                with self.assertRaises(ls.LogError):ls.ensure_provider()
                self.assertEqual(file.read_text(),'apiVersion: 1\nproviders: []\n')

    def test_provider_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'autokom.yaml'
            target=Path(folder)/'custom.yaml'
            target.write_text('operator config')
            path.symlink_to(target)
            with patch.object(ls,'GRAFANA_PROVIDER',path):
                with self.assertRaises(ls.LogError):ls.ensure_provider()
                self.assertEqual(target.read_text(),'operator config')

    def test_install_complete_detects_missing_provider_even_when_loki_healthy(self):
        with patch('logging_runtime.runtime_ready',return_value=True), \
             patch.object(ls,'GRAFANA_PROVIDER',Path('/nonexistent-v26-provider')):
            self.assertFalse(ls.install_complete('loki','docker'))
            self.assertFalse(ls.install_complete('loki','systemd'))

    def test_dashboard_db_zero_rows_is_inconclusive_not_absence(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'grafana.db'
            con=sqlite3.connect(db)
            con.executescript('CREATE TABLE data_source(uid text,type text,url text);CREATE TABLE dashboard(uid text,data text);')
            con.execute('INSERT INTO data_source VALUES(?,?,?)',('autokom-loki','loki',bridge.LOKI))
            con.commit();con.close()
            result=bridge.local_state(db)
            self.assertFalse(result['dashboards_authoritative'])
            with patch.object(bridge,'DASH',Path(folder)):
                (Path(folder)/'autokom-logging-health.json').write_text('{}')
                self.assertFalse(bridge.drift(result,Path(folder)))

    def test_check_reports_inconclusive_when_sqlite_does_not_prove_dashboards(self):
        state={'available':True,'datasource':{'uid':'autokom-loki','type':'loki','url':bridge.LOKI},
               'dashboards':{},'dashboards_authoritative':False}
        with patch.object(bridge,'report',return_value=(state,{'checked':False})), \
             patch.object(bridge,'PROVIDER',Path(__file__)), \
             patch.object(bridge,'DS',Path(__file__)), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.reconcile(apply=False),2)

    def test_db_with_dashboard_rows_detects_missing_managed_uids(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            (folder/'autokom-logging-health.json').write_text('{}')
            state={'available':True,'datasource':{'uid':'autokom-loki','type':'loki','url':bridge.LOKI},
                   'dashboards':{'another':{'queries':[]}},'dashboards_authoritative':True}
            self.assertTrue(any('autokom-logging-health' in i for i in bridge.drift(state,folder)))

    def test_no_restart_when_unchanged_and_verified(self):
        with patch.object(bridge,'DS',Path(__file__)),patch.object(bridge,'PROVIDER',Path(__file__)), \
             patch.object(bridge,'datasource_loaded',return_value=True), \
             patch.object(bridge.subprocess,'run') as run:
            run.return_value.returncode=0
            with redirect_stdout(io.StringIO()):self.assertTrue(bridge.finalize_install(changed=False))
            run.assert_called_once_with(['systemctl','is-active','--quiet','grafana-server'],capture_output=True,timeout=8)

    def test_changed_dashboard_triggers_one_grafana_restart_only(self):
        with patch.object(bridge,'DS',Path(__file__)),patch.object(bridge,'PROVIDER',Path(__file__)), \
             patch.object(bridge,'datasource_loaded',return_value=True), \
             patch.object(bridge,'request',return_value=({'database':'ok'},None)), \
             patch.object(bridge.subprocess,'run') as run:
            run.return_value.returncode=0
            with redirect_stdout(io.StringIO()):self.assertTrue(bridge.finalize_install(changed=True))
            operations=[c.args[0] for c in run.call_args_list]
            self.assertEqual(operations.count(['systemctl','restart','grafana-server']),1)
            self.assertNotIn(['systemctl','restart','loki'],operations)
            self.assertNotIn(['systemctl','restart','alloy'],operations)

    def test_native_reconcile_reuses_shared_grafana_completion(self):
        import logging_runtime as rt
        with patch.object(rt,'detected',return_value='systemd'), \
             patch.object(rt,'native_ready',return_value=True), \
             patch.object(ls,'provision_grafana',return_value=True) as pg, \
             patch('logging_monitoring.apply') as mon, \
             patch.object(ls,'sync_grafana_after_install') as sync:
            rt.migrate_to_native('loki')
            pg.assert_called_once();mon.assert_called_once();sync.assert_called_once_with(True)

    def test_docker_reconcile_reuses_shared_grafana_completion(self):
        from subprocess import CompletedProcess
        with patch.object(ls,'preflight'), patch.object(ls,'validate_alloy'), \
             patch.object(ls,'install_files',return_value='secret'), \
             patch.object(ls,'docker',return_value=['docker']), \
             patch.object(ls,'profile_args',return_value=[]), \
             patch.object(ls,'run',return_value=CompletedProcess([],0,'','')), \
             patch.object(ls,'provision_grafana',return_value=True) as pg, \
             patch('logging_monitoring.apply') as mon, \
             patch('logging_health.http_status',return_value={'ok':True}), \
             patch.object(ls,'sync_grafana_after_install') as sync:
            with redirect_stdout(io.StringIO()):self.assertEqual(ls.apply_docker('loki'),0)
            pg.assert_called_once();mon.assert_called_once();sync.assert_called_once_with(changed=True)

    def test_zero_error_chart_is_not_fake_log_entry(self):
        cicd=next(x for x in ld.dashboards() if x['uid']=='autokom-logging-cicd')
        self.assertEqual(cicd['panels'][1]['title'],'Applications Docker · journaux (tous les services)')
        self.assertEqual(cicd['panels'][1]['type'],'logs')
        self.assertEqual(cicd['panels'][2]['type'],'timeseries')
        self.assertIn('or vector(0)',cicd['panels'][2]['targets'][0]['expr'])

    def test_color_status_words_on_tty_without_touching_redirect(self):
        buffer=io.StringIO()
        colors.pretty_print('  ✓ Loki: UP; Grafana: OK; dashboards: ABSENT',file=buffer)
        self.assertNotIn('\x1b',buffer.getvalue())
        buffer.isatty=lambda:True
        with patch.dict(os.environ,{'TERM':'xterm-256color'}):
            colors.pretty_print('  ✓ Loki: UP; Grafana: OK; dashboards: ABSENT',file=buffer)
        self.assertIn('\033[',buffer.getvalue())
        self.assertIn('ABSENT',buffer.getvalue())

if __name__=='__main__': unittest.main()
