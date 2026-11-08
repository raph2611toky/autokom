"""Regression: convergent recovery must be safe, noninteractive and transparent."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import autokom_recover as rec
import autokom_frontend_manager as fm
import autokom_cli
import autokom_control_recovery as ctrl
import logging_runtime as lr
from autokom_runtime_integrity import Integrity
integrity_ok = Integrity(True, True, True, True, True)

class ACMEPreflightTests(unittest.TestCase):
    def test_success_creates_private_random_token_and_removes_it(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def fake_open(req, timeout):
                token=req.full_url.rsplit('/',1)[1]
                target=root/'.well-known/acme-challenge'/token
                self.assertEqual(req.get_header('Host'),'auto.ikom.mg')
                self.assertTrue(target.is_file())
                content=target.read_bytes()
                response=MagicMock(status=200)
                response.read.return_value=content
                response.__enter__.return_value=response
                return response
            with patch.object(fm, 'ACME_WEBROOT', root), \
                 patch('urllib.request.OpenerDirector.open',side_effect=fake_open):
                with redirect_stdout(io.StringIO()):fm.verify_acme_http('auto.ikom.mg')
            self.assertEqual(list((root/'.well-known/acme-challenge').iterdir()),[])

    def test_http_404_blocks_certbot_early_and_cleans_token(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            error=urllib.error.HTTPError('http://127.0.0.1/',404,'Not Found',{},None)
            with patch.object(fm, 'ACME_WEBROOT', root), \
                 patch('urllib.request.OpenerDirector.open',side_effect=error):
                with self.assertRaisesRegex(fm.FrontendError,'HTTP 404'):
                    fm.verify_acme_http('auto.ikom.mg')
            self.assertEqual(list((root/'.well-known/acme-challenge').iterdir()),[])

class RecoverFlowTests(unittest.TestCase):
    def test_backend_and_logs_plan_does_not_mutate(self):
        with patch.object(rec,'logging_target',return_value=('systemd','loki')), \
             patch.object(rec,'frontend_targets',return_value=('auto.ikom.mg','backend.auto.ikom.mg')), \
             patch('autokom_app_manager.verify_health',return_value=True), \
             patch('autokom_runtime_integrity.inspect',return_value=integrity_ok), \
             patch.object(ctrl,'inspect',return_value=ctrl.Health('active','active','ok',True)), \
             patch('logging_stack.install_complete',return_value=False), \
             patch.object(rec,'ready_frontend',return_value=False), \
             patch('logging_runtime.apply',side_effect=AssertionError('mutation')), \
             patch.object(fm,'setup',side_effect=AssertionError('mutation')), \
             patch.object(fm,'deploy',side_effect=AssertionError('mutation')):
            with redirect_stdout(io.StringIO()):self.assertEqual(rec.run(apply=False),0)

    def test_apply_repairs_logs_and_frontend_once(self):
        with patch.object(rec,'logging_target',return_value=('systemd','loki')), \
             patch.object(rec,'frontend_targets',return_value=('auto.ikom.mg','backend.auto.ikom.mg')), \
             patch('autokom_app_manager.verify_health',return_value=True), \
             patch('autokom_runtime_integrity.inspect',return_value=integrity_ok), \
             patch.object(ctrl,'inspect',return_value=ctrl.Health('active','active','ok',True)), \
             patch('logging_stack.install_complete',side_effect=[False,True]), \
             patch.object(rec,'ready_frontend',side_effect=[False,False,True]), \
             patch('logging_runtime.apply') as native, \
             patch.object(fm,'STATE') as state, \
             patch.object(fm,'setup') as setup, \
             patch.object(fm,'deploy') as deploy, \
             patch('os.geteuid',return_value=0):
            state.is_file.return_value=False
            deploy.return_value=0
            with redirect_stdout(io.StringIO()):self.assertEqual(rec.run(apply=True),0)
            native.assert_called_once_with('systemd','loki')
            setup.assert_called_once()
            deploy.assert_called_once()

    def test_loki_failure_does_not_prevent_independent_frontend(self):
        with patch.object(rec,'logging_target',return_value=('systemd','loki')), \
             patch.object(rec,'frontend_targets',return_value=('auto.ikom.mg','backend.auto.ikom.mg')), \
             patch('autokom_app_manager.verify_health',return_value=True), \
             patch('autokom_runtime_integrity.inspect',return_value=integrity_ok), \
             patch.object(ctrl,'inspect',return_value=ctrl.Health('active','active','ok',True)), \
             patch('logging_stack.install_complete',return_value=False), \
             patch.object(rec,'ready_frontend',side_effect=[False,False,True]), \
             patch('logging_runtime.apply',side_effect=lr.RuntimeErrorSafe('Loki fail')), \
             patch.object(fm,'STATE') as state, \
             patch.object(fm,'setup') as setup, \
             patch.object(fm,'deploy') as deploy, \
             patch('os.geteuid',return_value=0):
            state.is_file.return_value=False
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(rec.RecoverError,'Loki fail'):
                    rec.run(apply=True)
            setup.assert_called_once()
            deploy.assert_called_once()

    def test_cli_exposes_recover_without_installing(self):
        with redirect_stdout(io.StringIO()) as s:
            with self.assertRaises(SystemExit) as e:
                autokom_cli.main(['recover','--help'])
        self.assertEqual(e.exception.code,0)
        self.assertIn('--apply',s.getvalue())

    def test_fallback_mode_prefers_prepared_native_installation(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); (d/'.autokom-installed').write_text('managed')
            with patch('logging_runtime.detected',return_value='none'), \
                 patch('logging_runtime.NATIVE',d):
                self.assertEqual(rec.logging_target(),('systemd','loki'))

    def test_read_only_preflight_error_missing_env(self):
        with patch('autokom_frontend_manager.config_urls',side_effect=fm.FrontendError('invalid')):
            with self.assertRaisesRegex(rec.RecoverError,'refuse de deviner'):
                rec.frontend_targets()

if __name__=='__main__':unittest.main()
