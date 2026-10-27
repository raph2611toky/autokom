#!/usr/bin/env python3
"""AUTOKOM 0.1: version, signes, aide et progression sans effet systeme."""
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import autokom_cli as cli
import tool_banner as display


class Terminal(io.StringIO):
    def isatty(self):
        return True


class VersionTests(unittest.TestCase):
    def test_release_and_revision_separate_and_dynamic(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / 'VERSION').write_text('0.1\n')
            with patch.object(cli, 'ROOT', r), patch.object(cli, 'command', return_value=Mock(returncode=0, stdout='abc1234\n')):
                self.assertEqual(cli.version_label(), 'AUTOKOM · version 0.1\nAUTOKOM · révision abc1234')
            (r / 'VERSION').write_text('0.2.1\n')
            with patch.object(cli, 'ROOT', r), patch.object(cli, 'command', return_value=Mock(returncode=0, stdout='def7890\n')):
                self.assertEqual(cli.version_label(), 'AUTOKOM · version 0.2.1\nAUTOKOM · révision def7890')

    def test_release_missing_or_invalid_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            with patch.object(cli, 'ROOT', r):
                self.assertEqual(cli.release_version(), 'indisponible')
                (r / 'VERSION').write_text('\x1b[31mEXAMPLE\n')
                self.assertEqual(cli.release_version(), 'invalide (fichier VERSION)')

    def test_flags_top_level_and_each_subcommand_have_help_and_version(self):
        for base in ([], ['install'], ['status'], ['check'], ['config'], ['configure'], ['update']):
            for flag in ('-v', '--version', '-h', '--help'):
                with self.subTest(base=base, flag=flag), redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(SystemExit) as cm:
                        cli.make_parser().parse_args(base + [flag])
                    self.assertEqual(cm.exception.code, 0)
                    self.assertIn('AUTOKOM · version 0.1' if flag in ('-v', '--version') else 'usage:', out.getvalue())

    def test_help_never_queries_infrastructure(self):
        with patch.object(cli, 'snapshot', side_effect=AssertionError('probe')), \
             patch.object(cli, 'load_settings', side_effect=AssertionError('config')):
            for argv in (['status','-h'], ['install','--help'], ['configure','-v']):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(argv)


class PresentationTests(unittest.TestCase):
    def test_colored_symbols_tty_preserve_text_and_width(self):
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}, clear=False):
            os.environ.pop('NO_COLOR', None)
            for sign in ('+', '!', '-', '✓', '✗', '›', '?'):
                with self.subTest(sign=sign):
                    line=f'  {sign} quelques mots'
                    colored=display.colored_marker(line, stream=Terminal())
                    self.assertIn('\x1b[', colored)
                    self.assertTrue(colored.startswith('  \x1b['))
                    self.assertTrue(colored.endswith(' quelques mots'))
                    self.assertEqual(colored.replace(colored[2:colored.index(' quelques mots')], sign), line)

    def test_plain_output_no_ansi_when_redirected_no_color_or_dumb(self):
        plain='  ! attention'
        self.assertEqual(display.colored_marker(plain, stream=io.StringIO()), plain)
        for env in ({'NO_COLOR':'1', 'TERM':'xterm'}, {'TERM':'dumb'}):
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(display.colored_marker(plain, stream=Terminal()), plain)

    def test_progress_counts_completed_stages_never_elapsed_percentage(self):
        out=io.StringIO()
        display.phase_progress(1, 4, 'Fondation', stream=out)
        display.phase_progress(2, 4, 'Jenkins', skipped=True, stream=out)
        text=out.getvalue()
        self.assertIn('Étapes 1/4', text)
        self.assertIn('Étapes 2/4', text)
        self.assertIn('  - Étapes 2/4', text)
        self.assertNotIn('%', text)
        self.assertNotIn('\x1b', text)

    def test_progress_invalid_count_rejected(self):
        with self.assertRaises(ValueError):
            display.phase_progress(5, 4, 'Bad')

    def test_progress_tty_bar(self):
        out=Terminal()
        with patch.dict(os.environ, {'TERM':'xterm'}, clear=False):
            os.environ.pop('NO_COLOR', None)
            display.phase_progress(3, 4, 'Observabilité', stream=out)
        self.assertIn('3/4', out.getvalue())
        self.assertIn('━', out.getvalue())
        self.assertIn('\x1b[', out.getvalue())

    def test_spinner_never_outputs_in_logs_and_propagates_exception(self):
        out=io.StringIO()
        with self.assertRaisesRegex(RuntimeError, 'failure'):
            with display.Activity('read-only probe', delay=0.0, stream=out):
                raise RuntimeError('failure')
        self.assertEqual(out.getvalue(), '')

    def test_spinner_is_just_temporary_terminal_animation(self):
        out=Terminal()
        with patch.dict(os.environ, {'TERM':'xterm'}, clear=False):
            os.environ.pop('NO_COLOR', None)
            with display.Activity('Inventaire', delay=0.002, interval=0.005, stream=out):
                time.sleep(0.04)
        self.assertIn('Inventaire', out.getvalue())
        self.assertTrue(out.getvalue().endswith('\r\x1b[2K'))


if __name__ == '__main__':
    unittest.main()
