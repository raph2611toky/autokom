"""Regression: broker missing => API 503, never false-positive production ready.

Mocks isolate systemd; these tests must not start services or edit real units.
"""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_control_recovery as cr
import autokom_recover as recover
import autokom_runtime_admin as admin

OK = cr.Health('active','active','ok',True)
MISSING = cr.Health('inactive','inactive','broker-inactive',False)
NO_SOCKET = cr.Health('active','active','socket-missing',False)


class ControlRecoveryTests(unittest.TestCase):
    def test_healthy_control_does_not_restart_or_install_services(self):
        with patch.object(cr,'inspect',return_value=OK), \
             patch.object(cr,'_run',side_effect=AssertionError('systemd mutation')), \
             patch.object(cr,'_install_missing_unit',side_effect=AssertionError('unit mutation')):
            with redirect_stdout(io.StringIO()):
                self.assertTrue(cr.reconcile(apply=True).ready)

    def test_check_does_not_change_anything_even_if_missing(self):
        with patch.object(cr,'inspect',return_value=MISSING), \
             patch.object(cr,'_environment_preflight',side_effect=AssertionError('preflight')), \
             patch.object(cr,'_run',side_effect=AssertionError('systemd')):
            with redirect_stdout(io.StringIO()):
                self.assertFalse(cr.reconcile(apply=False).ready)

    def test_missing_broker_and_worker_recovered_once(self):
        issued=[]
        def command(argv, **_kwargs):
            issued.append(argv)
            return subprocess.CompletedProcess(argv,0,'','')
        with patch.object(cr,'inspect',side_effect=[MISSING,OK]), \
             patch.object(cr,'_environment_preflight'), \
             patch.object(cr,'_installed',return_value=False), \
             patch.object(cr,'_install_missing_unit') as installed, \
             patch.object(cr,'_run',side_effect=command), \
             patch.object(cr,'_wait_for_broker',return_value=True), \
             patch.object(cr,'_active',return_value='inactive'), \
             patch.object(cr.os,'geteuid',return_value=0):
            with redirect_stdout(io.StringIO()):
                self.assertTrue(cr.reconcile(apply=True).ready)
        self.assertEqual(installed.call_count,2)
        self.assertEqual(sum(args[:2] == ['systemctl','daemon-reload'] for args in issued),1)
        self.assertEqual(sum(args[:3] == ['systemctl','enable','--now'] for args in issued),2)
        self.assertNotIn('ufw',repr(issued))

    def test_active_broker_missing_socket_gets_one_controlled_restart(self):
        issued=[]
        with patch.object(cr,'inspect',side_effect=[NO_SOCKET,OK]), \
             patch.object(cr,'_environment_preflight'), \
             patch.object(cr,'_installed',return_value=True), \
             patch.object(cr,'_run',side_effect=lambda argv,**kwargs:(issued.append(argv),subprocess.CompletedProcess(argv,0,'',''))[1]), \
             patch.object(cr,'_wait_for_broker',return_value=True), \
             patch.object(cr,'_active',return_value='active'), \
             patch.object(cr.os,'geteuid',return_value=0):
            with redirect_stdout(io.StringIO()):
                self.assertTrue(cr.reconcile(apply=True).ready)
        self.assertEqual(issued,[['systemctl','restart',cr.BROKER_UNIT]])

    def test_suspicious_socket_permissions_do_not_trigger_chown(self):
        bad=cr.Health('active','inactive','socket-permissions',False)
        with patch.object(cr,'inspect',return_value=bad), \
             patch.object(cr,'_environment_preflight'), \
             patch.object(cr,'_installed',return_value=True), \
             patch.object(cr,'_run',side_effect=AssertionError('no mutation')), \
             patch.object(cr.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(cr.ControlRecoveryError,'Permissions/socket'):
                with redirect_stdout(io.StringIO()):cr.reconcile(apply=True)

    def test_missing_secrets_fails_closed_without_starting_services(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'missing-runtime.env'
            with patch.object(cr,'RUNTIME_ENV',p):
                with self.assertRaisesRegex(cr.ControlRecoveryError,'Secrets du broker absents'):
                    cr._environment_preflight()

    def test_socket_ping_probe_is_syntax_valid_and_has_no_exec_shell(self):
        compile(cr.PROBE_CODE,'<broker-ping>','exec')
        self.assertIn('sock.connect',cr.PROBE_CODE)
        self.assertIn('runuser',Path(cr.__file__).read_text())
        self.assertNotIn('shell=True',Path(cr.__file__).read_text())

    def test_app_deploy_checks_control_plane_after_http_health(self):
        code=(ROOT/'scripts/lib/autokom_app_manager.py').read_text()
        self.assertLess(code.index("with phase('Disponibilité · réponse HTTP /api/health/')"),
                        code.index("with phase('Contrôle des déploiements · broker Unix et worker')"))
        self.assertIn('reconcile(apply=True)',code)

    def test_runtime_reconcile_exposes_check_apply(self):
        out=io.StringIO()
        with redirect_stdout(out),self.assertRaises(SystemExit) as result:
            admin.main(['reconcile','--help'])
        self.assertEqual(result.exception.code,0)
        self.assertIn('--apply',out.getvalue())
        self.assertIn('--check',out.getvalue())

    def test_existing_local_unit_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'broker.service'; source.write_text('sample')
            dest=Path(td)/cr.BROKER_UNIT;dest.write_text('custom-override')
            with patch.object(cr,'UNIT_DIR',Path(td)):
                with self.assertRaisesRegex(cr.ControlRecoveryError,'Service déjà présent'):
                    cr._install_missing_unit(cr.BROKER_UNIT,source)
            self.assertEqual(dest.read_text(),'custom-override')

    def test_auto_recover_reports_control_as_error_not_success(self):
        frontend='auto.ikom.mg'; backend='backend.auto.ikom.mg'
        with patch.object(recover,'logging_target',return_value=('systemd','loki')), \
             patch.object(recover,'frontend_targets',return_value=(frontend,backend)), \
             patch('autokom_app_manager.verify_health',return_value=True), \
             patch('logging_stack.install_complete',return_value=True), \
             patch.object(recover,'ready_frontend',return_value=True), \
             patch.object(cr,'inspect',return_value=MISSING), \
             patch.object(cr,'reconcile',side_effect=cr.ControlRecoveryError('broker absent')), \
             patch.object(recover.os,'geteuid',return_value=0):
            with redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(recover.RecoverError,'broker absent'):
                    recover.run(apply=True)
            self.assertIn('Déploiements=ERREUR',output.getvalue())


if __name__=='__main__': unittest.main()
