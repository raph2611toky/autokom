#!/usr/bin/env python3
"""AUTOKOM branding: CLI UX, aliases, colorful compact headers."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import autokom_cli as cli
import tool_banner


class Terminal(io.StringIO):
    def isatty(self):
        return True


class FlagsTests(unittest.TestCase):
    def test_top_level_version_aliases(self):
        for flag in ('-v', '--version'):
            with self.subTest(flag=flag), redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as result:
                    cli.make_parser().parse_args([flag])
                self.assertEqual(result.exception.code, 0)
                self.assertIn('AUTOKOM ·', out.getvalue())

    def test_top_level_help_aliases(self):
        for flag in ('-h', '--help'):
            with self.subTest(flag=flag), redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as result:
                    cli.make_parser().parse_args([flag])
                self.assertEqual(result.exception.code, 0)
                self.assertIn('status', out.getvalue())
                self.assertIn('install', out.getvalue())

    def test_subcommand_help_and_version_aliases(self):
        for name in ('install', 'status', 'check', 'config', 'configure', 'update'):
            for flag in ('-v', '--version', '-h', '--help'):
                with self.subTest(command=name, flag=flag), redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(SystemExit) as result:
                        cli.make_parser().parse_args([name, flag])
                    self.assertEqual(result.exception.code, 0)
                    self.assertIn('AUTOKOM ·' if 'v' in flag or flag == '--version' else 'usage:', out.getvalue())

    def test_help_version_do_not_probe_services(self):
        with patch.object(cli, 'snapshot', side_effect=AssertionError('snapshot forbidden')), \
             patch.object(cli, 'load_settings', side_effect=AssertionError('settings forbidden')):
            for argv in (['-v'], ['--help'], ['status', '-v'], ['install', '-h']):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        cli.main(argv)
                    self.assertEqual(result.exception.code, 0)

    def test_normal_argument_parsing_never_calls_git(self):
        with patch.object(cli, 'command', side_effect=AssertionError('Git called needlessly')):
            for argv in (['status'], ['check'], ['install'], ['install', '--check'],
                         ['config'], ['configure', 'plugins']):
                self.assertIsNotNone(cli.make_parser().parse_args(argv))
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.make_parser().parse_args(['--help'])

    def test_git_revision_is_dynamic(self):
        with patch.object(cli, 'command', side_effect=[Mock(returncode=0, stdout='02ad048\n'),
                                                    Mock(returncode=0, stdout='v2.0.0\n')]):
            self.assertEqual(cli.version_label(), 'AUTOKOM · révision 02ad048')
            self.assertEqual(cli.version_label(), 'AUTOKOM · révision v2.0.0')

    def test_actual_banner_script_used_not_new_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'scripts').mkdir()
            script = Path(tmp) / 'scripts/autokom-banner.sh'
            script.write_text('#!/usr/bin/env bash\necho AUTHENTIC-BANNER\n')
            with patch.object(cli, 'ROOT', Path(tmp)), \
                 patch.object(cli.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
                 redirect_stdout(io.StringIO()):
                cli.show_brand()
            run.assert_called_once_with(['bash', str(script)], check=False, timeout=5)

    def test_banner_failure_falls_back_without_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cli, 'ROOT', Path(tmp)), redirect_stdout(io.StringIO()) as out:
                cli.show_brand()
            self.assertIn('AUTOKOM', out.getvalue())


class FrameTests(unittest.TestCase):
    def test_every_service_has_distinct_color_when_tty(self):
        lines = {}
        with patch.dict(os.environ, {'TERM':'xterm-256color'}, clear=False):
            for name in ('Jenkins', 'Prometheus', 'Grafana', 'Fondation', 'Sécurité'):
                out = Terminal()
                with redirect_stdout(out):
                    tool_banner.section(name, 'Test')
                self.assertIn('\x1b[', out.getvalue())
                self.assertIn('╭', out.getvalue())
                self.assertNotIn('████', out.getvalue())
                lines[name] = out.getvalue().split('╭',1)[0]
        self.assertEqual(len(set(lines.values())), len(lines))

    def test_no_color_env_forces_plain_even_tty(self):
        out = Terminal()
        with patch.dict(os.environ, {'TERM': 'xterm', 'NO_COLOR': ''}), redirect_stdout(out):
            tool_banner.section('Prometheus', 'Status')
        self.assertNotIn('\x1b[', out.getvalue())
        self.assertIn('PROMETHEUS', out.getvalue())

    def test_redirection_disables_ansi(self):
        out = io.StringIO()
        with patch.dict(os.environ, {'TERM':'xterm-256color'}, clear=False), redirect_stdout(out):
            tool_banner.section('Jenkins', 'Audit')
        self.assertNotIn('\x1b[', out.getvalue())

    def test_unknown_color_falls_back(self):
        out = Terminal()
        with patch.dict(os.environ, {'TERM': 'xterm'}), redirect_stdout(out):
            tool_banner.section('AUTOKOM', 'Statut', tone='unknown')
        self.assertIn('AUTOKOM', out.getvalue())
        self.assertIn('\x1b[', out.getvalue())


if __name__ == '__main__':
    unittest.main()
