"""Regression tests for second-resolution backup collisions and native service preparation.

Tests use temporary directories and never invoke live systemd, Docker or APT.
"""
from __future__ import annotations
import datetime
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import logging_runtime as runtime


class NativeBackupTests(unittest.TestCase):
    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 8, 4, 46, 0, tzinfo=tz)

    def test_second_collision_preserves_original_and_creates_unique_paths(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / 'logs'
            with patch.object(runtime, 'BACKUP_ROOT', base), \
                 patch.object(runtime.datetime, 'datetime', self.FrozenDatetime):
                original = runtime.new_backup()
                (original / 'original.txt').write_text('NE PAS SUPPRIMER')
                second = runtime.new_backup()
                third = runtime.new_backup()
            self.assertEqual(original.name, '20261108T044600Z')
            self.assertEqual(second.name, '20261108T044600Z-0001')
            self.assertEqual(third.name, '20261108T044600Z-0002')
            self.assertEqual((original / 'original.txt').read_text(), 'NE PAS SUPPRIMER')
            self.assertEqual(len(list(base.iterdir())), 3)
            self.assertEqual(base.stat().st_mode & 0o777, 0o700)

    def test_existing_backup_name_is_never_reused(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            existing = base / '20261108T044600Z'
            existing.mkdir()
            (existing / 'logs-native').mkdir()
            (existing / 'logs-native' / 'loki.yaml').write_text('original')
            with patch.object(runtime, 'BACKUP_ROOT', base), \
                 patch.object(runtime.datetime, 'datetime', self.FrozenDatetime):
                new = runtime.new_backup()
            self.assertEqual(new.name, '20261108T044600Z-0001')
            self.assertEqual((existing / 'logs-native' / 'loki.yaml').read_text(), 'original')

    def test_refuse_symlink_backup_root(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            actual = base / 'actual'; actual.mkdir()
            alias = base / 'logs'; alias.symlink_to(actual, target_is_directory=True)
            with patch.object(runtime, 'BACKUP_ROOT', alias):
                with self.assertRaisesRegex(runtime.RuntimeErrorSafe, 'symbolique'):
                    runtime.new_backup()
            self.assertEqual(list(actual.iterdir()), [])

    def test_no_backup_modification_in_dry_run(self):
        with patch.object(runtime, 'detected', return_value='systemd'), \
             patch.object(runtime, 'new_backup', side_effect=AssertionError('dry-run modified backup')):
            with redirect_stdout(io.StringIO()):
                runtime.plan('systemd')


class NativePreparationTests(unittest.TestCase):
    def test_old_config_is_backed_up_once_even_if_both_files_are_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root/'native'; native.mkdir()
            config = root/'config'
            data = root/'data'
            old_data = root/'old-data'
            marker = native/'.autokom-installed'; marker.write_text('managed')
            with patch.object(runtime, 'DATA', data), \
                 patch.object(runtime, 'LEGACY_DATA', old_data):
                expected = runtime.native_files()
            for name, value in expected.items():
                (native/name).write_text(value)
            saved = []
            def fake_save(path, content, mode=0o644):
                path = Path(path)
                if not str(path).startswith('/etc/systemd/'):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                saved.append((str(path), content))
            with patch.object(runtime, 'NATIVE', native), \
                 patch.object(runtime, 'CONFIG_DIR', config), \
                 patch.object(runtime, 'DATA', data), \
                 patch.object(runtime, 'LEGACY_DATA', old_data), \
                 patch.object(runtime, 'new_backup', return_value=root/'backup') as snapshot, \
                 patch.object(runtime, 'backup_configs') as copy, \
                 patch.object(runtime, 'cmd', return_value=Mock(returncode=0)), \
                 patch.object(runtime.shutil, 'which', side_effect=lambda n: '/usr/bin/'+n), \
                 patch.object(runtime, 'secure_save', side_effect=fake_save):
                with redirect_stdout(io.StringIO()):
                    runtime.prepare_native()
            snapshot.assert_called_once()
            copy.assert_called_once_with(root/'backup')
            self.assertEqual((config/'loki.yaml').read_text(), expected['loki.yaml'])
            self.assertEqual((config/'config.alloy').read_text(), expected['config.alloy'])
            env = (native/'alloy.env').read_text()
            self.assertIn('STORAGE_DIRECTORY="/var/lib/alloy/data"', env)
            self.assertIn('CONFIG_FILE="'+str(config/'config.alloy')+'"', env)
            self.assertTrue((native/'loki.yaml').is_file())  # legacy file preserved
            self.assertEqual((native/'.autokom-installed').read_text(), 'loki=3.7.0\nalloy=1.11.0\n')

    def test_permissions_use_unprivileged_daemon_identity(self):
        success = Mock(returncode=0)
        with patch.object(runtime, 'cmd', return_value=success) as calls:
            runtime.validate_native_permissions()
        cmds = [call.args for call in calls.call_args_list]
        self.assertEqual(len(cmds), 3)
        self.assertIn(('runuser', '-u', 'autokom-loki', '--', 'test', '-r',
                       str(runtime.CONFIG_DIR/'loki.yaml')), cmds)
        self.assertIn(('runuser', '-u', 'autokom-loki', '--', 'test', '-w',
                       str(runtime.DATA)), cmds)
        self.assertIn(('runuser', '-u', 'alloy', '--', 'test', '-r',
                       str(runtime.CONFIG_DIR/'config.alloy')), cmds)

    def test_permission_error_stops_startup(self):
        with patch.object(runtime, 'cmd', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(runtime.RuntimeErrorSafe, 'Droits autokom-loki insuffisants'):
                runtime.validate_native_permissions()




class DiagnosticsTests(unittest.TestCase):
    def test_crash_hints_without_reprinting_sensitive_journal_lines(self):
        def fake(*args, **kwargs):
            if args[2] == 'loki.service':
                return Mock(returncode=0, stdout='failed to load config: token=SECRET_VALUE\n')
            return Mock(returncode=0, stdout='permission denied: password=PRIVATE_VALUE\n')
        with patch.object(runtime, 'cmd', side_effect=fake):
            msg = runtime.native_journal_hints()
        self.assertIn('loki: fichier de configuration inaccessible', msg)
        self.assertIn('alloy: permissions', msg)
        self.assertNotIn('SECRET_VALUE', msg)
        self.assertNotIn('PRIVATE_VALUE', msg)

if __name__ == '__main__':
    unittest.main()
