"""Non-regression de l'ajout `autokom iac install` sans mutation GCP."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import autokom_iac as iac
import autokom_cli as cli


class IACToolsTests(unittest.TestCase):
    def test_cli_advertises_iac(self):
        parser = cli.make_parser()
        self.assertEqual(parser.parse_args(["iac"]).cmd, "iac")
        self.assertIn("'iac'", (ROOT / "scripts/lib/autokom_cli.py").read_text())

    def test_install_all_runs_separate_playbook_through_script(self):
        with patch.object(iac.os, "geteuid", return_value=0), \
             patch.object(iac.subprocess, "run") as fake, \
             patch.object(iac, "show_status") as status:
            fake.return_value.returncode = 0
            self.assertEqual(iac.main(["install"]), 0)
            self.assertEqual(fake.call_args.args[0][0], "bash")
            self.assertEqual(fake.call_args.args[0][-2:], ["--apply", "all"])
            status.assert_called_once()

    def test_install_check_is_read_only(self):
        with patch.object(iac.os, "geteuid", return_value=0), \
             patch.object(iac.subprocess, "run") as fake, \
             patch.object(iac, "show_status") as status:
            fake.return_value.returncode = 0
            self.assertEqual(iac.main(["install", "--only", "terraform", "--check"]), 0)
            self.assertEqual(fake.call_args.args[0][-2:], ["--check", "terraform"])
            status.assert_not_called()

    def test_gcloud_only_is_separate(self):
        with patch.object(iac.os, "geteuid", return_value=0), \
             patch.object(iac.subprocess, "run") as fake, \
             patch.object(iac, "show_status"):
            fake.return_value.returncode = 0
            self.assertEqual(iac.main(["install", "--only", "gcloud"]), 0)
            self.assertEqual(fake.call_args.args[0][-2:], ["--apply", "gcloud"])

    def test_non_root_install_is_refused(self):
        with patch.object(iac.os, "geteuid", return_value=1000):
            with self.assertRaises(iac.IACError):
                iac.main(["install"])

    def test_install_error_propagates(self):
        with patch.object(iac.os, "geteuid", return_value=0), \
             patch.object(iac.subprocess, "run") as fake:
            fake.return_value.returncode = 42
            with self.assertRaises(iac.IACError):
                iac.main(["install"])

    def test_iac_roles_are_separate_and_not_retrofitted_to_foundation(self):
        source = (ROOT / "ansible/playbooks/iac-tools.yml").read_text()
        self.assertIn("role: terraform_cli", source)
        self.assertIn("role: gcloud_cli", source)
        self.assertIn("when: autokom_iac_component", source)
        self.assertNotIn("terraform_cli", (ROOT / "ansible/playbooks/foundation.yml").read_text())

    def test_official_apt_sources_and_no_cloud_provisioning(self):
        terraform = (ROOT / "ansible/roles/terraform_cli/tasks/main.yml").read_text()
        gcloud = (ROOT / "ansible/roles/gcloud_cli/tasks/main.yml").read_text()
        for source in (terraform, gcloud):
            self.assertIn("not ansible_check_mode", source)
            self.assertIn("signed-by", source)
            self.assertNotIn("terraform apply", source)
            self.assertNotIn("ansible.builtin.command:\n    argv: [gcloud, auth, login]", source)
            self.assertNotIn("state: absent", source)
        self.assertIn("https://apt.releases.hashicorp.com", terraform)
        self.assertIn("https://packages.cloud.google.com/apt", gcloud)
        self.assertIn("google-cloud-cli", gcloud)
        self.assertIn("name: terraform", terraform)
        self.assertLess(terraform.index("Refuser les dépôts"), terraform.index("Installer les prérequis"))
        self.assertLess(gcloud.index("Refuser les dépôts"), gcloud.index("Installer les prérequis"))

    def test_shell_has_read_only_dry_run_and_no_terraform_apply(self):
        shell = (ROOT / "scripts/install/iac.sh").read_text()
        self.assertIn("--check", shell)
        self.assertIn("--apply", shell)
        self.assertIn('ARGS+=( --check )', shell)
        self.assertNotIn("terraform apply", shell)
        self.assertNotIn("gcloud auth login", shell)


if __name__ == "__main__":
    unittest.main()
