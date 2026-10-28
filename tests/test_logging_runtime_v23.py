"""v23: runtime choice safety / non-destructive dry-run regression tests."""
from __future__ import annotations
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import logging_runtime as r
import autokom_cli as cli
import logging_stack

class RuntimeV23(unittest.TestCase):
    def test_detect_existing_docker(self):
        with patch.object(r,'active_native',return_value=False),patch.object(r,'active_docker',return_value=True):
            self.assertEqual(r.detected(),'docker')

    def test_detect_existing_systemd(self):
        with patch.object(r,'active_native',return_value=True),patch.object(r,'active_docker',return_value=False):
            self.assertEqual(r.detected(),'systemd')

    def test_conflict_cannot_choose(self):
        with patch.object(r,'active_native',return_value=True),patch.object(r,'active_docker',return_value=True):
            self.assertEqual(r.detected(),'mixed')
            with self.assertRaises(r.RuntimeErrorSafe): r.apply('systemd','loki')

    def test_no_implicit_backend_dual_native(self):
        with self.assertRaises(r.RuntimeErrorSafe):r.require_loki_only('both')
        with self.assertRaises(r.RuntimeErrorSafe):r.require_loki_only('opensearch')

    def test_native_configs_use_host_loopback_and_data_dir(self):
        data=r.native_files()
        self.assertIn('127.0.0.1:3100/loki/api/v1/push',data['config.alloy'])
        self.assertNotIn('http://loki:3100/loki/api/v1/push',data['config.alloy'])
        self.assertIn('http_listen_address: 127.0.0.1',data['loki.yaml'])
        self.assertIn('/var/lib/autokom/loki/chunks',data['loki.yaml'])

    def test_docker_config_is_backward_compatible(self):
        self.assertIn('http://loki:3100/loki/api/v1/push',logging_stack.alloy_config('loki'))
        self.assertIn('127.0.0.1:3100:3100',logging_stack.compose('loki'))

    def test_dry_run_has_no_mutations(self):
        with patch.object(r,'detected',return_value='docker'),patch.object(r,'cmd',side_effect=AssertionError('unexpected subprocess')):
            stream=io.StringIO()
            with redirect_stdout(stream):r.plan('systemd')
            self.assertIn('sauvegarder',stream.getvalue().lower())
            self.assertIn('Docker',stream.getvalue())

    def test_interactive_choice_uses_shared_menu(self):
        with patch.object(r,'detected',return_value='docker'),patch('menu.pick',return_value=2) as pick:
            with redirect_stdout(io.StringIO()):selected=cli.choose_logs_runtime(None)
        self.assertEqual(selected,'systemd')
        self.assertIn('Choisir le mode',pick.call_args.args[0])

    def test_explicit_choice_skips_menu(self):
        with patch('menu.pick',side_effect=AssertionError('no prompt expected')):
            self.assertEqual(cli.choose_logs_runtime('docker'),'docker')

    def test_non_tty_refuses_implicit_mode_selection(self):
        with patch.object(r,'detected',return_value='docker'),patch('menu.pick',side_effect=RuntimeError('no tty')):
            with self.assertRaises(cli.CLIError):cli.choose_logs_runtime(None)

    def test_status_runtime_no_apply(self):
        stream=io.StringIO()
        with patch.object(r,'report') as report,patch.object(r,'apply',side_effect=AssertionError('mutation')):
            with redirect_stdout(stream):
                self.assertEqual(logging_stack.main(['runtime']),0)
        report.assert_called_once()

    def test_cli_installer_exposes_runtime_flag(self):
        # parse only: no infrastructure changes
        with redirect_stdout(io.StringIO()) as out:
            try:cli.main(['install','--help'])
            except SystemExit as e:self.assertEqual(e.code,0)
        self.assertIn('--runtime',out.getvalue())

    def test_runtime_command_applies_only_with_apply_flag(self):
        with patch('logging_runtime.plan') as plan,patch('logging_runtime.apply') as apply:
            self.assertEqual(logging_stack.main(['runtime','systemd','--check']),0)
        plan.assert_called_once_with('systemd')
        apply.assert_not_called()

if __name__=='__main__':unittest.main()
