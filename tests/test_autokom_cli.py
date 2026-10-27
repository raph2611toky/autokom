#!/usr/bin/env python3
"""Tests: CLI, plan adaptatif, simulation sans effet, reprise et listener."""
from dataclasses import replace
from pathlib import Path
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_cli as cli
import jenkins_network as net


def server(**overrides):
    base = cli.Snapshot(
        foundation_ok=True, jenkins_installed=True, jenkins_active=True,
        jenkins_private=False, jenkins_listening=True,
        jenkins_plugins_missing=('pipeline-stage-view',),
        prometheus_active=False, prometheus_private=False,
        grafana_active=False, grafana_private=False,
        metrics_up=False, datasource_ok=False, ufw_active=False)
    return replace(base, **overrides)


class PlanTests(unittest.TestCase):
    def test_real_aws_case(self):
        plan = cli.actions(server())
        self.assertFalse(plan['foundation'])
        self.assertTrue(plan['services'])
        self.assertTrue(plan['plugins'])
        self.assertTrue(plan['observability'])

    def test_everything_compliant_skips_all(self):
        state = server(jenkins_private=True, jenkins_plugins_missing=(),
                       prometheus_active=True, prometheus_private=True,
                       grafana_active=True, grafana_private=True,
                       metrics_up=True, datasource_ok=True, ufw_active=True)
        self.assertFalse(any(cli.actions(state).values()))

    def test_misconfigured_domain_requires_reconcile(self):
        state = server(jenkins_private=True, jenkins_plugins_missing=(),
                       prometheus_active=True, grafana_active=True,
                       metrics_up=True, datasource_ok=True, domains_ready=False)
        self.assertTrue(cli.actions(state)['observability'])

    def test_start_point(self):
        p = cli.make_parser()
        self.assertEqual(cli.selected(p.parse_args(['install','--from','plugins'])),
                         ('plugins', 'observability'))
        self.assertEqual(cli.selected(p.parse_args(['install','--only','observability'])),
                         ('observability',))
        with self.assertRaises(cli.CLIError):
            cli.selected(p.parse_args(['install','--only','services','--from','plugins']))

    def test_check_never_calls_mutating_module(self):
        with patch.object(cli, 'snapshot', return_value=server()), \
             patch.object(cli, 'apply_stage', side_effect=AssertionError('mutation')), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['install','--check']), 0)
            self.assertIn('Simulation en lecture seule', output.getvalue())

    def test_skip_compliant_stages_apply(self):
        state = server(jenkins_private=True, jenkins_plugins_missing=(),
                       prometheus_active=True, grafana_active=True,
                       metrics_up=True, datasource_ok=True)
        with patch.object(cli, 'snapshot', return_value=state), \
             patch.object(cli, 'apply_stage', side_effect=AssertionError('should skip')), \
             patch.object(cli.os, 'geteuid', return_value=0), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['install']), 0)

    def test_target_stage_applied_once(self):
        state = server()
        clean = server(jenkins_private=True, jenkins_plugins_missing=(),
                       prometheus_active=True, grafana_active=True, metrics_up=True,
                       datasource_ok=True)
        with patch.object(cli, 'snapshot', side_effect=[state, state, clean]) as snap, \
             patch.object(cli, 'apply_stage') as apply, \
             patch.object(cli.os, 'geteuid', return_value=0), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['install','--only','observability']), 0)
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0], 'observability')
        self.assertEqual(snap.call_count, 3)

    def test_no_direct_execution_of_legacy_numbered_scripts(self):
        names = ['scripts/bootstrap/preflight.sh', 'scripts/bootstrap/ansible.sh',
                 'scripts/install/foundation.sh', 'scripts/install/services.sh',
                 'scripts/install/jenkins.sh', 'scripts/install/observability.sh',
                 'scripts/verify/stack.py']
        for name in names:
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file())
                self.assertNotRegex(Path(name).name, r'^\d+-')


class JenkinsNetworkTests(unittest.TestCase):
    def test_private_address_classification(self):
        self.assertTrue(net.private_listener(['127.0.0.1:8080', '[::1]:8080']))
        self.assertFalse(net.private_listener(['*:8080']))
        self.assertFalse(net.private_listener(['127.0.0.1:8080', '0.0.0.0:8080']))
        self.assertFalse(net.private_listener([]))

    def test_unchanged_no_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'override.conf'
            exec_ = Mock()
            result = net.enforce(8080, approved=False, dropin=path,
                                 read_bindings=lambda p: ['127.0.0.1:8080'], executor=exec_)
            self.assertEqual(result, 'unchanged')
            exec_.assert_not_called()
            self.assertFalse(path.exists())

    def test_exposed_requires_explicit_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'override.conf'
            with self.assertRaises(net.NetworkError):
                net.enforce(8080, approved=False, dropin=path,
                            read_bindings=lambda p: ['*:8080'], executor=Mock())
            self.assertFalse(path.exists())

    def test_unmanaged_override_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'override.conf'
            path.write_text('foreign content')
            with self.assertRaises(net.NetworkError):
                net.enforce(8080, approved=True, dropin=path,
                            read_bindings=lambda p: ['*:8080'], executor=Mock())
            self.assertEqual(path.read_text(), 'foreign content')

    def test_applies_when_runtime_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'override.conf'
            read = Mock(side_effect=[['*:8080'], ['127.0.0.1:8080']])
            exec_ = Mock()
            self.assertEqual(net.enforce(8080, approved=True, dropin=path,
                                         read_bindings=read, executor=exec_), 'fixed')
            self.assertIn('JENKINS_LISTEN_ADDRESS=127.0.0.1', path.read_text())
            self.assertEqual(exec_.call_count, 3)

    def test_rolls_back_on_restart_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'override.conf'
            original = net.MARKER + '\n[Service]\nEnvironment="X=1"\n'
            path.write_text(original)
            commands = []
            def execute(*args, **kw):
                commands.append(args)
                if len(commands) == 2:
                    raise net.NetworkError('failed restart')
            with self.assertRaises(net.NetworkError):
                net.enforce(8080, approved=True, dropin=path,
                            read_bindings=lambda p: ['*:8080'], executor=execute)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(len(commands), 4)


if __name__ == '__main__':
    unittest.main()
