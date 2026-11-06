"""Prevent a green CI when ingress exists but is disconnected from the broker.
No Docker, Nginx, Jenkins, network, or privileged operations are executed.
"""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / 'scripts/lib/autokom_platform_broker.py'
ADMIN = ROOT / 'scripts/lib/autokom_runtime_admin.py'

class ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker = BROKER.read_text(encoding='utf-8')
        cls.admin = ADMIN.read_text(encoding='utf-8')
        ast.parse(cls.broker)
        ast.parse(cls.admin)

    def test_internal_ingress_is_really_called_by_broker(self):
        self.assertIn('import autokom_internal_ingress as ingress', self.broker)
        self.assertIn('relay = ingress.create(', self.broker)
        self.assertIn('ingress.preflight(run, PROXY, ENABLED)', self.broker)

    def test_no_host_port_exposure_in_docker_pipeline(self):
        self.assertNotIn('--publish', self.broker)
        self.assertIn("'--network',network", self.broker)

    def test_ingress_cleanup_and_reconciliation_are_wired(self):
        self.assertIn('ingress.remove(', self.broker)
        self.assertIn('_ingress_watchdog', self.broker)

    def test_runtime_doctor_checks_ingress(self):
        self.assertIn('private ingress doctor', self.admin)
        self.assertIn('ingress_report = diagnose(', self.admin)

if __name__ == '__main__':
    unittest.main()
