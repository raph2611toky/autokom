"""Contrats de changement minimal : delai de grace MIG sans changer les probes."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'terraform/modules/gcp_regional_ha'
ENV = ROOT / 'terraform/environments/gcp-ha'


class TestHAWorkerDelay(unittest.TestCase):
    def test_worker_window_only_for_non_validation(self):
        source = (MOD / 'main.tf').read_text()
        self.assertIn('var.bootstrap_mode == "validation" ? 360 : var.worker_initial_delay_seconds', source)
        self.assertNotIn('initial_delay_sec = 360', source)

    def test_non_worker_window_defaults_to_30_minutes(self):
        for path in (MOD / 'variables.tf', ENV / 'variables.tf'):
            source = path.read_text()
            self.assertIn('variable "worker_initial_delay_seconds"', source)
            block = source.split('variable "worker_initial_delay_seconds"', 1)[1]
            self.assertRegex(block, r'default\s*=\s*1800')
            self.assertIn('<= 3600', block)
            self.assertIn('floor(var.worker_initial_delay_seconds)', block)

    def test_value_is_forwarded_and_example_updated(self):
        self.assertIn('worker_initial_delay_seconds = var.worker_initial_delay_seconds', (ENV / 'main.tf').read_text())
        self.assertIn('worker_initial_delay_seconds = 1800', (ENV / 'terraform.tfvars.example').read_text())

    def test_healthcheck_not_relaxed_or_removed(self):
        main = (MOD / 'main.tf').read_text()
        self.assertIn('google_compute_health_check.web.id', main)
        self.assertIn('unhealthy_threshold = 3', main)
        self.assertIn('healthy_threshold   = 2', main)
        probe = (MOD / 'files/worker-health.py').read_text()
        self.assertIn('503', probe)
        self.assertIn('worker-ready', probe)

    def test_no_changes_to_bootstrap_script(self):
        script = (MOD / 'files/worker-startup.sh.tftpl').read_text()
        self.assertIn('apt-get -y', script)
        self.assertIn('AUTOKOM_HA_WORKER_PREPARED', script)

if __name__ == '__main__':
    unittest.main()
