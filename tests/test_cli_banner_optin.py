#!/usr/bin/env python3
"""The big AUTOKOM logo is opt-in on every CLI invocation."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_cli as cli


class BannerOptInTests(unittest.TestCase):
    def test_parser_recognizes_banner_before_and_after_subcommand(self):
        for name in ('install', 'status', 'check', 'config', 'configure', 'update'):
            for flag in ('-b', '--with-banner'):
                for arguments in ([flag, name], [name, flag]):
                    if name == 'configure':
                        arguments = arguments + ['plugins']
                    with self.subTest(argv=arguments):
                        namespace = cli.make_parser().parse_args(arguments)
                        self.assertTrue(namespace.with_banner)

    def test_default_is_no_banner_for_all_commands(self):
        for name in ('install', 'status', 'check', 'config', 'configure', 'update'):
            args = [name] + (['plugins'] if name == 'configure' else [])
            with self.subTest(argv=args):
                self.assertFalse(cli.make_parser().parse_args(args).with_banner)

    def test_no_banner_for_default_help_and_version(self):
        for args in ([], ['-h'], ['--help'], ['-v'], ['--version'],
                     ['install', '-h'], ['install', '--version'],
                     ['status', '-h'], ['check', '--help'],
                     ['config', '-v'], ['configure', '--help'], ['update', '-h']):
            with self.subTest(args=args), patch.object(cli, 'show_brand') as show:
                with redirect_stdout(io.StringIO()):
                    try:
                        cli.main(args)
                    except SystemExit as exc:
                        self.assertEqual(exc.code, 0)
                show.assert_not_called()

    def test_banner_help_version_all_orderings(self):
        combinations = (['-b', '-v'], ['--with-banner', '--help'],
                        ['-v', '-b'], ['--help', '--with-banner'],
                        ['-b', 'install', '-h'], ['install', '-b', '--help'],
                        ['install', '--version', '-b'],
                        ['--with-banner', 'status', '--help'],
                        ['status', '--with-banner', '-v'],
                        ['configure', 'plugins', '-b', '-h'])
        for args in combinations:
            with self.subTest(args=args), patch.object(cli, 'show_brand') as show:
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as exc:
                        cli.main(args)
                self.assertEqual(exc.exception.code, 0)
                show.assert_called_once_with()

    def test_no_banner_for_status_check_or_install_plan(self):
        # Do not touch a real infrastructure during a banner UX test.
        cfg = object()
        with patch.object(cli, 'load_settings', return_value=cfg), \
             patch.object(cli, 'brief', return_value=0) as brief, \
             patch.object(cli, 'perform', return_value=0) as perform, \
             patch.object(cli, 'show_brand') as show:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['status']), 0)
                self.assertEqual(cli.main(['install', '--check']), 0)
            show.assert_not_called()
            brief.assert_called_once_with(cfg)
            self.assertTrue(perform.called)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['status', '--with-banner']), 0)
                self.assertEqual(cli.main(['-b', 'install', '--check']), 0)
            self.assertEqual(show.call_count, 2)

    def test_brand_not_triggered_after_option_terminator(self):
        # Even on errors, a positional-looking '-b' after -- is not treated as opt-in.
        with patch.object(cli, 'show_brand') as show, redirect_stdout(io.StringIO()):
            with patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    cli.main(['status', '--', '-b'])
        show.assert_not_called()


if __name__ == '__main__':
    unittest.main()
