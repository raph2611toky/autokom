"""Regression guard for the supported Google provider MIG rollout configuration."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'terraform/modules/gcp_regional_ha/main.tf'


class HAStandardProviderCompatibility(unittest.TestCase):
    def test_standard_google_mig_avoids_beta_only_min_ready_sec(self):
        text = MAIN.read_text(encoding='utf-8')
        self.assertIn('resource "google_compute_region_instance_group_manager" "web"', text)
        self.assertNotRegex(text, r'(?m)^\s*min_ready_sec\s*=')

    def test_rolling_update_keeps_zero_planned_unavailability(self):
        text = MAIN.read_text(encoding='utf-8')
        self.assertRegex(text, r'(?s)update_policy\s*\{[^}]*type\s*=\s*"PROACTIVE"')
        self.assertRegex(text, r'(?s)update_policy\s*\{[^}]*replacement_method\s*=\s*"SUBSTITUTE"')
        self.assertRegex(text, r'(?s)update_policy\s*\{[^}]*max_surge_fixed\s*=\s*2')
        self.assertRegex(text, r'(?s)update_policy\s*\{[^}]*max_unavailable_fixed\s*=\s*0')
        self.assertIn('auto_healing_policies', text)


if __name__ == '__main__':
    unittest.main()
