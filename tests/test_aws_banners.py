#!/usr/bin/env python3
"""AUTOKOM banners and read-only audit tests (no AWS required)."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/lib'))
import tool_banner as banners

class BannerTests(unittest.TestCase):
    def setUp(self):
        banners._SEEN.clear()
    def test_original_logos_no_color(self):
        for tool in ('jenkins','prometheus','grafana','kibana','wazuh'):
            with self.subTest(tool=tool):
                file=ROOT/'scripts/banners'/f'{tool}.py'
                p=subprocess.run([sys.executable,str(file),'--no-color','--no-title'],
                                 capture_output=True,text=True,timeout=12)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertNotIn('\x1b[',p.stdout)
                self.assertGreaterEqual(len(p.stdout.splitlines()),15)
                self.assertLessEqual(max(map(len,p.stdout.splitlines())),65)
    def test_no_log_spam(self):
        with redirect_stdout(io.StringIO()) as f:
            self.assertFalse(banners.render('jenkins'))
        self.assertEqual(f.getvalue(),'')
    def test_force_and_once(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertTrue(banners.render('grafana',force=True,no_color=True))
            self.assertFalse(banners.render('grafana',force=True,no_color=True))
        self.assertIn('G R A F A N A',out.getvalue())
        self.assertNotIn('\x1b[',out.getvalue())
    def test_off(self):
        with patch.dict('os.environ',{'AUTOKOM_BANNERS':'off'}):
            self.assertFalse(banners.render('wazuh',force=True))
    def test_unknown_rejected(self):
        with self.assertRaises(ValueError):
            banners.render('inconnu',force=True)

class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('autokom_audit',ROOT/'scripts/verify/09-check-stack.py')
        cls.m=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
    def test_loopback(self):
        for addr in ('127.0.0.1:8080','[::1]:3000'):
            self.assertTrue(self.m.is_loopback(addr))
        for addr in ('0.0.0.0:8080','*:3000','[::]:9090','172.31.21.188:9090'):
            self.assertFalse(self.m.is_loopback(addr))
    def test_exposure_detected(self):
        r=self.m.Report()
        with patch.object(self.m,'port_addresses',return_value=['0.0.0.0:9090']):
            with redirect_stdout(io.StringIO()):
                self.m.check_bind(r,'Prometheus',9090)
        self.assertEqual(r.rows[0][0],'FAIL')
    def test_missing_is_warning_or_failure(self):
        for strict,expected in ((False,'WARN'),(True,'FAIL')):
            r=self.m.Report(strict)
            with patch.object(self.m,'port_addresses',return_value=[]):
                with redirect_stdout(io.StringIO()):
                    self.m.check_bind(r,'Grafana',3000)
            self.assertEqual(r.rows[0][0],expected)

if __name__=='__main__':
    unittest.main()
