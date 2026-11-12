"""Regression: keep the GCP free-trial sizing coherent across AUTOKOM."""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class GCPTrial70GB(unittest.TestCase):
    def test_balanced_ssd_and_safe_surging_are_preserved(self):
        source = _text('terraform/modules/gcp_regional_ha/main.tf')
        self.assertRegex(source, r'(?m)^\s*disk_type\s*=\s*"pd-balanced"\s*$')
        self.assertNotRegex(source, r'(?m)^\s*min_ready_sec\s*=')
        self.assertRegex(source, r'(?m)^\s*max_surge_fixed\s*=\s*2\s*$')
        self.assertRegex(source, r'(?m)^\s*max_unavailable_fixed\s*=\s*0\s*$')

    def test_defaults_and_documented_config_match_70gb_and_2_to_5(self):
        env = _text('terraform/environments/gcp-ha/variables.tf')
        module = _text('terraform/modules/gcp_regional_ha/variables.tf')
        for source in (env, module):
            self.assertRegex(source, r'variable "boot_disk_size_gb"\s*\{[^}]*\bdefault\s*=\s*70')
            self.assertRegex(source, r'variable "min_replicas"\s*\{[^}]*\bdefault\s*=\s*2')
            self.assertRegex(source, r'variable "max_replicas"\s*\{[^}]*\bdefault\s*=\s*5')
        example = _text('terraform/environments/gcp-ha/terraform.tfvars.example')
        self.assertRegex(example, r'(?m)^boot_disk_size_gb\s*=\s*70\s*$')
        self.assertRegex(example, r'(?m)^min_replicas\s*=\s*2\s*$')
        self.assertRegex(example, r'(?m)^max_replicas\s*=\s*5\s*$')

    def test_upgrade_command_and_app_allocator_share_limits(self):
        p = ROOT / 'scripts/lib/autokom_ha_infra_upgrade.py'
        spec = importlib.util.spec_from_file_location('ha_trial_upgrade', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.SAFETY['boot_disk_size_gb'], '70')
        self.assertEqual(mod.SAFETY['min_replicas'], '2')
        self.assertEqual(mod.SAFETY['max_replicas'], '5')
        sample = 'machine_type = "e2-small"\nmin_replicas = 2\nmax_replicas = 6\n'
        output = mod.render_tfvars(sample)
        self.assertIn('boot_disk_size_gb = 70', output)
        self.assertIn('max_replicas = 5', output)
        controller = _text('scripts/ha/autokom_ha_delivery.py')
        self.assertRegex(controller, r'(?m)^MAX_WORKERS\s*=\s*5\s*$')


if __name__ == '__main__':
    unittest.main()
