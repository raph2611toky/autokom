"""AUTOKOM HA: backups do not block git update, preflight avoids quota deadlocks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
UPGRADE = ROOT / 'scripts/lib/autokom_ha_infra_upgrade.py'
UPDATE = ROOT / 'scripts/lib/autokom_private_update.py'


def load(path, alias):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ha = load(UPGRADE, 'ha_upgrade_backup_test')
updater = load(UPDATE, 'ha_update_backup_test')


class HABackupAndQuota(unittest.TestCase):
    def test_git_ignores_legacy_backup_but_still_blocks_real_edits(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home)
            def git(*args):
                return subprocess.run(['git', *args], cwd=path, check=True,
                                      capture_output=True, text=True).stdout.strip()
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'test')
            git('config', 'user.email', 'test@example.invalid')
            ignore = path / 'terraform/.gitignore'
            ignore.parent.mkdir(parents=True)
            ignore.write_text((ROOT / 'terraform/.gitignore').read_text())
            (path / 'core.txt').write_text('base')
            git('add', '.')
            git('commit', '-qm', 'base')
            local = path / 'terraform/environments/gcp-ha'
            local.mkdir(parents=True)
            (local / 'terraform.tfvars.bak-20261112-173859').write_text('old backup')
            (local / 'terraform.tfvars').write_text('protected local secrets')
            updater._clean(path)
            (path / 'core.txt').write_text('modified')
            with self.assertRaisesRegex(updater.UpdateError, 'Dépôt modifié'):
                updater._clean(path)
            (path / 'core.txt').write_text('base')
            (path / 'scripts').mkdir()
            (path / 'scripts/untracked.py').write_text('print(1)')
            with self.assertRaisesRegex(updater.UpdateError, 'Dépôt modifié'):
                updater._clean(path)

    def test_backup_is_outside_repository_and_private(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home)
            source = base / 'repo/terraform/environments/gcp-ha/terraform.tfvars'
            source.parent.mkdir(parents=True)
            source.write_text('before')
            backupdir = base / 'private-backups'
            target = ha.update_file(source, 'after', backup_dir=backupdir)
            self.assertEqual(source.read_text(), 'after')
            self.assertEqual(target.read_text(), 'before')
            self.assertEqual(target.parent, backupdir)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backupdir.stat().st_mode & 0o777, 0o700)
            self.assertIsNone(ha.update_file(source, 'after', backup_dir=backupdir))

    def test_symbolic_backup_directory_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home)
            source = base / 'terraform.tfvars'
            source.write_text('before')
            link = base / 'unsafe'
            link.symlink_to(base)
            with self.assertRaisesRegex(ha.UpgradeError, 'lien symbolique'):
                ha.update_file(source, 'after', backup_dir=link)
            self.assertEqual(source.read_text(), 'before')

    def test_quota_parse_and_guard(self):
        entry = [{'metric': 'SSD_TOTAL_GB', 'usage': 390.0, 'limit': 500.0}]
        row = ha.read_quota(entry)
        self.assertEqual(row['required'], 140)
        self.assertEqual(row['free'], 110)
        self.assertFalse(row['sufficient'])
        self.assertTrue(ha.read_quota([{'metric':'SSD_TOTAL_GB', 'usage':350, 'limit':500}])['sufficient'])
        with self.assertRaisesRegex(ha.UpgradeError, 'SSD_TOTAL_GB'):
            ha.read_quota([])

    def test_quota_block_does_not_modify_tfvars(self):
        original = 'project_id = "plucky-stacker-480421-c4"\nregion = "us-central1"\n'
        with mock.patch.object(ha, 'checked', return_value=json.dumps({'quotas': [
                 {'metric':'SSD_TOTAL_GB', 'usage':499, 'limit':500}]})):
            with self.assertRaisesRegex(ha.UpgradeError, 'insuffisant'):
                ha.check_ssd_quota(original, fatal=True)
        self.assertEqual(ha.SAFETY['boot_disk_size_gb'], '70')
        self.assertEqual(ha.SAFETY['max_replicas'], '5')

    def test_preview_plan_override_does_not_change_source(self):
        calls=[]
        with tempfile.TemporaryDirectory() as temp:
            def fake_checked(cmd, **kwargs):
                calls.append(cmd)
                if cmd[1:3] == ['show', '-json']:
                    return json.dumps({'resource_changes': []})
                return ''
            with mock.patch.object(ha, 'checked', side_effect=fake_checked):
                path, changes = ha.terraform_plan(temp, overrides=True)
        self.assertEqual(changes, [])
        self.assertIn('preview.tfplan', path)
        self.assertIn('-var', calls[0])
        self.assertIn('boot_disk_size_gb=70', calls[0])
        self.assertIn('max_replicas=5', calls[0])


if __name__ == '__main__':
    unittest.main()
