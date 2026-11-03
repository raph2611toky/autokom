"""Test CLI lifecycle without root, services, network or writes to /etc."""
from pathlib import Path
from unittest import mock
import importlib.util
import contextlib
import io
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_app_manager.py'
spec = importlib.util.spec_from_file_location('autokom_lifecycle_ui_test', PATH)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class LifecycleUITests(unittest.TestCase):
    def test_semgrep_uses_local_rules_not_auto_metrics_off(self):
        with tempfile.TemporaryDirectory() as root:
            backend=Path(root); (backend/'manage.py').write_text('')
            rules=backend/'rules.yml'; rules.write_text('rules: []\n')
            with mock.patch.object(app,'BACKEND',backend), \
                 mock.patch.object(app,'SECURITY_RULES',rules), \
                 mock.patch.object(app,'VENV',backend), \
                 mock.patch.object(app.shutil,'which', return_value='/usr/bin/tool'), \
                 mock.patch.object(app,'call') as call, \
                 mock.patch.object(app,'run_as_service'):
                (backend/'bin').mkdir(); (backend/'bin/python').write_text('')
                app.run_checks()
                cmd=call.call_args_list[0].args[0]
                self.assertEqual(cmd[:2],['semgrep','scan'])
                self.assertEqual(cmd[cmd.index('--config')+1],str(rules))
                self.assertEqual(cmd[cmd.index('--metrics')+1],'off')
                self.assertNotIn('auto',cmd)

    def test_status_inactive_does_not_raise_and_prints_remedy(self):
        with mock.patch.object(app,'load_state', return_value={'runtime':'systemd','domain':'backend.auto.ik0m.tech'}), \
             mock.patch.object(app.subprocess,'run',return_value=mock.Mock(stdout='inactive\n',returncode=3)), \
             mock.patch.object(app,'verify_health',return_value=False), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(app.status(None),1)
        self.assertIn('sudo autokom app deploy',buf.getvalue())
        self.assertNotIn('Échec de systemctl',buf.getvalue())

    def test_progress_capture_and_error_does_not_print_raw_logs(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            with mock.patch.object(app,'OPERATION_LOGS',path), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout:
                with app.operation('test'):
                    with app.phase('Test silencieux'):
                        app.call(['python3','-c','print("RAW-BACKGROUND-LOG")'],timeout=20)
            logs=list(path.glob('test-*.log'))
            self.assertEqual(len(logs),1)
            self.assertEqual(logs[0].stat().st_mode & 0o777,0o600)
            self.assertIn('RAW-BACKGROUND-LOG',logs[0].read_text())
            self.assertNotIn('RAW-BACKGROUND-LOG',stdout.getvalue())
            self.assertIn('Test silencieux',stdout.getvalue())

    def test_progress_exit_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(app,'OPERATION_LOGS',Path(root)), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(app.AppError,'Échec'):
                    with app.operation('test'):
                        with app.phase('Scanner'):
                            app.call(['python3','-c','import sys;sys.exit(4)'],timeout=20)

    def test_journal_options_are_visible(self):
        with self.assertRaises(SystemExit) as err,contextlib.redirect_stdout(io.StringIO()) as stdout:
            app.main(['logs','-h'])
        self.assertEqual(err.exception.code,0)
        self.assertIn('--source',stdout.getvalue())

if __name__ == '__main__': unittest.main()
