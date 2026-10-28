"""Regression tests for low-RAM selection, log service inventory and CLI aliases."""
from pathlib import Path
import contextlib
import io
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import autokom_cli as cli
import tools_registry as registry
import logging_stack as logging

GIB = 1024**3


class LoggingInteractiveUX(unittest.TestCase):
    def test_low_ram_menu_choose_loki_or_opensearch(self):
        import menu
        for number, expected in ((1,'loki'),(2,'opensearch')):
            with self.subTest(number=number), \
                 mock.patch.object(logging,'platform_resources',return_value=(int(7.6*GIB), 50*GIB)), \
                 mock.patch.object(menu,'pick',return_value=number) as selector, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.choose_logs_backend('both'), expected)
                self.assertIn('7.6 Gio', output.getvalue())
                self.assertEqual(len(selector.call_args.args[1]),2)
                self.assertIn('cancel_label', selector.call_args.kwargs)

    def test_8_to_12_gib_can_explicitly_keep_both(self):
        import menu
        with mock.patch.object(logging,'platform_resources',return_value=(10*GIB, 50*GIB)), \
             mock.patch.object(menu,'pick',return_value=3) as selector, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.choose_logs_backend('both'),'both')
            self.assertEqual(len(selector.call_args.args[1]),3)

    def test_unattended_requires_explicit_backend(self):
        import menu
        with mock.patch.object(logging,'platform_resources',return_value=(int(7.6*GIB),50*GIB)), \
             mock.patch.object(menu,'pick',side_effect=RuntimeError('TTY inaccessible')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(cli.CLIError, '--backend loki'):
                cli.choose_logs_backend('both')

    def test_explicit_loki_never_prompts(self):
        import menu
        with mock.patch.object(menu,'pick',side_effect=AssertionError('prompt forbidden')):
            self.assertEqual(cli.choose_logs_backend('loki'), 'loki')

    def test_small_ram_no_unsafe_opensearch_option(self):
        import menu
        with mock.patch.object(logging,'platform_resources',return_value=(3*GIB,50*GIB)), \
             mock.patch.object(menu,'pick',return_value=1) as selector, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.choose_logs_backend('both'),'loki')
            self.assertEqual(len(selector.call_args.args[1]),1)
            self.assertNotIn('Data Prepper', str(selector.call_args))

    def test_cancel_menu_preserves_no_side_effect(self):
        import menu
        with mock.patch.object(logging,'platform_resources',return_value=(int(7.6*GIB),50*GIB)), \
             mock.patch.object(menu,'pick',return_value=0), \
             mock.patch.object(cli,'call_module',side_effect=AssertionError('install forbidden')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                cli.choose_logs_backend('both')

    def test_unusable_ram_denies_even_loki(self):
        import menu
        with mock.patch.object(logging,'platform_resources',return_value=(int(.5*GIB),50*GIB)), \
             mock.patch.object(menu,'pick',side_effect=AssertionError('should never prompt')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(cli.CLIError,'minimum 1 Gio'):
                cli.choose_logs_backend('both')

    def test_optional_install_positional_and_no_ambiguous_targets(self):
        parser=cli.make_parser()
        self.assertEqual(cli.selected(parser.parse_args(['install','logs'])),('logs',))
        self.assertEqual(cli.selected(parser.parse_args(['install','logging'])),('logs',))
        with self.assertRaisesRegex(cli.CLIError,'une seule cible'):
            cli.selected(parser.parse_args(['install','logs','--only','logging']))

    def test_inventory_registry_lists_all_five_missing(self):
        with mock.patch.object(registry,'service_state',return_value='absent'), \
             contextlib.redirect_stdout(io.StringIO()) as all_output:
            registry.list_tools(all_tools=True)
        for name in ('loki','alloy','opensearch','data-prepper','opensearch-dashboards'):
            self.assertIn(name,all_output.getvalue())
        self.assertIn('06 · Journalisation',all_output.getvalue())
        with mock.patch.object(registry,'service_state',return_value='absent'), \
             contextlib.redirect_stdout(io.StringIO()) as normal:
            registry.list_tools()
        self.assertNotIn('06 · Journalisation',normal.getvalue())

    def test_compose_service_states_no_config_mutation(self):
        with mock.patch.object(registry.shutil,'which',return_value='/usr/bin/docker'), \
             mock.patch.object(registry,'proc') as run:
            for state, expected in (('running','active'),('exited','inactive'),('','absent')):
                run.return_value=mock.Mock(returncode=0,stdout=state+'\n' if state else '')
                self.assertEqual(registry.logging_docker_state('loki'), expected)
            command=run.call_args.args[0]
            self.assertEqual(command[:2],['docker','ps'])
            self.assertIn('label=com.docker.compose.project=autokom-logs',command)

    def test_services_all_and_tools_show_all_aliases(self):
        for args in (['services','--all'], ['tools','show','--all']):
            with self.subTest(args=args), \
                 mock.patch.object(registry,'list_tools') as listing:
                self.assertEqual(cli.main(args),0)
                listing.assert_called_once_with(all_tools=True)


if __name__=='__main__':
    unittest.main()

