"""Tests locaux du garde-fou de suppression Core de `autokom update`.

Pas de réseau, pas de secrets, ni de modification de l'environnement serveur.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/lib/autokom_private_update.py'
spec = importlib.util.spec_from_file_location('autokom_update_guard_test', SCRIPT)
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


class UpdateRemovalGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q', '-b', 'main')
        git(self.root, 'config', 'user.name', 'AUTOKOM test')
        git(self.root, 'config', 'user.email', 'tests@example.invalid')
        for entry in update.REQUIRED_CORE:
            target = self.root / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('necessary core\n', encoding='utf-8')
        for entry in ('scripts/dev/platform-runtime.env.example',
                      'scripts/dev/an-unrelated.env.example',
                      'scripts/lib/critical_service.py',
                      'platform/backend/.env.example'):
            target = self.root / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('placeholder only\n', encoding='utf-8')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'baseline')
        self.old = git(self.root, 'rev-parse', 'HEAD')

    def remove_and_commit(self, *paths):
        for item in paths:
            (self.root / item).unlink()
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'remove requested files')
        return git(self.root, 'rev-parse', 'HEAD')

    def test_approved_example_removal_does_not_block(self):
        new = self.remove_and_commit('scripts/dev/platform-runtime.env.example',
                                     'platform/backend/.env.example')
        self.assertIsNone(update._check_target(self.root, self.old, new))

    def test_unrelated_example_under_scripts_remains_protected(self):
        new = self.remove_and_commit('scripts/dev/an-unrelated.env.example')
        with self.assertRaisesRegex(update.UpdateError, 'suppression'):
            update._check_target(self.root, self.old, new)

    def test_real_core_code_deletion_is_blocked_alongside_approved(self):
        new = self.remove_and_commit('scripts/dev/platform-runtime.env.example',
                                     'scripts/lib/critical_service.py')
        with self.assertRaisesRegex(update.UpdateError, 'critical_service.py'):
            update._check_target(self.root, self.old, new)

    def test_required_core_missing_still_rejected(self):
        new = self.remove_and_commit('scripts/lib/autokom_cli.py')
        with self.assertRaisesRegex(update.UpdateError, 'incomplète'):
            update._check_target(self.root, self.old, new)

    def test_only_one_explicit_exception(self):
        self.assertEqual(update.APPROVED_NON_CORE_REMOVALS,
                         frozenset({'scripts/dev/platform-runtime.env.example'}))


if __name__ == '__main__':
    unittest.main()
