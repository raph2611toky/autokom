"""AUTOKOM tools and v9 UX safety regression tests; no mutations of host."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_cli as cli
import tools_registry as tools
import observability as obs

class ToolsCLI(unittest.TestCase):
    def test_tools_and_services_help_without_probing(self):
        for label in ('tools','services'):
            for suffix in (['-h'],['show','-h'],['change','-h'],['list','-h'],['restart','-h']):
                with self.subTest(label=label,suffix=suffix),contextlib.redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(SystemExit) as exit:
                        cli.main([label]+suffix)
                    self.assertEqual(exit.exception.code,0)
                    self.assertIn('usage:',out.getvalue())

    def test_cli_subcommands_parse_and_alias(self):
        parser=cli.make_parser()
        for prefix in ('tools','services'):
            arg=parser.parse_args([prefix,'show','jenkins','-e'])
            self.assertTrue(arg.environments)
            arg=parser.parse_args([prefix,'show','jenkins','-E'])
            self.assertTrue(arg.environments_values)
            self.assertEqual(parser.parse_args([prefix,'change','prometheus','port=9091','--apply']).settings,['port=9091'])

    def test_show_paths_only_no_file_contents(self):
        with mock.patch.object(tools,'service_state',return_value='active'):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                tools.show('jenkins')
        self.assertIn('/var/lib/jenkins/config.xml',out.getvalue())
        self.assertIn('/etc/systemd/system/jenkins.service.d',out.getvalue())
        self.assertNotIn('hudson.security',out.getvalue())

    def test_env_names_only_never_values(self):
        with mock.patch.object(tools,'effective_env',return_value={'TOKEN':'SECRET-VALUE','USER':'jenkins'}),\
             contextlib.redirect_stdout(io.StringIO()) as out:
            tools.environments('jenkins',values=False)
        self.assertIn('TOKEN',out.getvalue());self.assertNotIn('SECRET-VALUE',out.getvalue())

    def test_env_values_gate_never_output_without_tty(self):
        with mock.patch.object(tools.os,'geteuid',return_value=0),\
             mock.patch.object(tools,'confirm_sensitive',side_effect=tools.ToolError('non-interactif')):
            with self.assertRaises(tools.ToolError):
                tools.environments('jenkins',values=True)

    def test_no_ufw_shutdown(self):
        with mock.patch.object(tools.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(tools.ToolError,'UFW'):
                tools.service_action('ufw','stop')

    def test_change_is_preview_only_by_default(self):
        with mock.patch.object(tools,'load_observability') as config,\
             mock.patch.object(tools,'confirm_action',side_effect=AssertionError('No prompt')),\
             contextlib.redirect_stdout(io.StringIO()) as out:
            tools.change('prometheus',['port=9091'])
        self.assertIn('Simulation',out.getvalue())
        config.assert_called_once_with(changes=['prometheus.port=9091'])

    def test_change_refuses_secrets_and_jenkins_port(self):
        for name,key in (('grafana','admin_password'),('prometheus','token'),('jenkins','port')):
            with self.subTest(name=name,key=key):
                with self.assertRaises(tools.ToolError):
                    tools.change(name,[f'{key}=SECRET'])

    def test_service_restart_never_runs_without_confirmation(self):
        with mock.patch.object(tools.os,'geteuid',return_value=0),\
             mock.patch.object(tools,'service_state',return_value='active'),\
             mock.patch.object(tools,'confirm_action',return_value=False),\
             mock.patch.object(tools,'proc',side_effect=AssertionError('Should not restart')):
            with self.assertRaisesRegex(tools.ToolError,'annulée'):
                tools.service_action('jenkins','restart')

class GitColors(unittest.TestCase):
    def test_git_stat_has_actual_green_and_red_in_terminal(self):
        stat=' file.py | 6 ++++--\n 1 file changed, 4 insertions(+), 2 deletions(-)\n'
        output=cli.color_git_diffstat(stat,enabled=True)
        self.assertIn('\x1b[32m++++\x1b[0m',output)
        self.assertIn('\x1b[31m--\x1b[0m',output)
        self.assertEqual(cli.color_git_diffstat(stat,enabled=False),stat)
        self.assertIn('file.py',output)

    def test_does_not_recolor_filename_hyphens(self):
        raw=' foo---bar.py | 3 ++-\n'
        out=cli.color_git_diffstat(raw,enabled=True)
        self.assertIn('foo---bar.py',out)

class AptPreflight(unittest.TestCase):
    def test_prometheus_public_no_apache_install_attempted(self):
        fake = type('Args',(),{'prometheus_domain':'prometheus.example.org','grafana_domain':'', 'mode':'apply'})()
        with mock.patch.object(obs.os,'geteuid',return_value=0), \
             mock.patch.object(obs.Path,'is_file',return_value=True), \
             mock.patch.object(obs.Path,'read_text',return_value='ID=ubuntu'), \
             mock.patch.object(obs.shutil,'which',return_value='/usr/bin/tool'), \
             mock.patch.object(obs,'listener',return_value=[]), \
             mock.patch.object(obs,'run',side_effect=AssertionError('APT must not start')):
            obs.preflight(fake)

class PrometheusAuthentication(unittest.TestCase):
    def test_uses_openssl_stdin_no_apache_apt(self):
        with mock.patch.object(obs,'PROM_HTPASSWD',Path('/tmp/nonexistent-autokom-v9-htpasswd')):
            with mock.patch.object(obs,'owned_write',return_value=True) as write,\
                 mock.patch.object(obs.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'$6$salt$hash\n','')) as exec_:
                result=obs.ensure_prom_basic_auth('metricsadmin','secretpass12345678')
        self.assertTrue(result)
        cmd=exec_.call_args.args[0]
        self.assertEqual(cmd,['openssl','passwd','-6','-stdin'])
        self.assertNotIn('secretpass12345678',repr(cmd))
        self.assertEqual(write.call_args.args[1],'metricsadmin:$6$salt$hash\n')

if __name__=='__main__':unittest.main()
