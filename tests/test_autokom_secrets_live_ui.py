"""No MySQL, GitHub, root, or systemd required."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('autokom_app_manager_secrets_test', ROOT/'scripts/lib/autokom_app_manager.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class TTY(io.StringIO):
    def isatty(self):
        return True


class Tests(unittest.TestCase):
    def test_live_preview_has_three_green_lines_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.log'
            p.write_text('step\nfirst\nsecond\nthird\nfourth\n', encoding='utf-8')
            live=app.LiveProgress('deploy')
            live._tty=True
            live.log_path=p
            live._preview_start=0
            with mock.patch.object(app.sys,'stdout', TTY()) as stdout:
                live.paint('SAST',3)
                shown=stdout.getvalue()
                self.assertIn('\x1b[32m', shown)
                self.assertIn('second', shown)
                self.assertIn('third', shown)
                self.assertIn('fourth', shown)
                self.assertNotIn('first', shown)
                live.clear()
                self.assertFalse(live._visible)

    def test_sensitive_preview_is_masked(self):
        self.assertIn('masquée', app.LiveProgress._safe_line('PASSWORD=supersecret'))
        self.assertIn('masquée', app.LiveProgress._safe_line('Authorization: Bearer abc123'))
        self.assertEqual(app.LiveProgress._safe_line('\x1b[2Jhello\r'), 'hello')

    def test_subprocess_output_durable_but_not_broadcast_without_tty(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(app,'OPERATION_LOGS',Path(temp)), redirect_stdout(io.StringIO()) as stdout:
                with app.operation('test'):
                    with app.phase('test log'):
                        app.call(['python3','-c','print("LOG-LINE-SHOULD-BE-IN-FILE")'], timeout=20)
            logs=list(Path(temp).glob('test-*.log'))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
            self.assertIn('LOG-LINE-SHOULD-BE-IN-FILE', logs[0].read_text())
            self.assertNotIn('LOG-LINE-SHOULD-BE-IN-FILE', stdout.getvalue())

    def test_migration_exists_and_exposes_encrypted_value(self):
        path=ROOT/'platform/backend/apps/projects/secrets/migrations/0002_encrypted_secret_value.py'
        self.assertIn("name='encrypted_value'",path.read_text())


if __name__ == '__main__':
    unittest.main()
