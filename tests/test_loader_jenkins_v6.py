#!/usr/bin/env python3
"""Regression tests: Java IPv4-mapped listener, spinner lifecycle, Git update."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_cli as cli
import jenkins_network as network


class JenkinsAddressTests(unittest.TestCase):
    def test_loopback_ipv4_ipv6_and_java_mapped_ipv4(self):
        for addrs in (['127.0.0.1:8080'], ['[::1]:8080'],
                      ['[::ffff:127.0.0.1]:8080'],
                      ['::ffff:127.0.0.1:8080'],
                      ['127.0.0.1:8080', '[::ffff:127.0.0.1]:8080']):
            with self.subTest(addrs=addrs):
                self.assertTrue(network.private_listener(addrs))

    def test_public_or_ambiguous_address_is_not_private(self):
        for addrs in ([], ['*:8080'], ['0.0.0.0:8080'],
                      ['[::]:8080'], ['[::ffff:0.0.0.0]:8080'],
                      ['172.31.21.188:8080'], ['[fe80::1]:8080'],
                      ['127.0.0.1:8080', '*:8080'], ['example.com:8080'],
                      ['127.0.0.1:999999'], ['missing-port']):
            with self.subTest(addrs=addrs):
                self.assertFalse(network.private_listener(addrs))

    def test_enforce_accepts_mapped_loopback_no_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'jenkins.conf'
            samples = iter((['*:8080'], ['[::ffff:127.0.0.1]:8080']))
            ex = Mock(return_value='')
            self.assertEqual('fixed', network.enforce(8080, approved=True,
                dropin=path, read_bindings=lambda p: next(samples), executor=ex))
            self.assertIn('JENKINS_LISTEN_ADDRESS=127.0.0.1', path.read_text())
            self.assertEqual([c.args[:2] for c in ex.call_args_list],
                             [('systemctl', 'daemon-reload'),
                              ('systemctl', 'restart'),
                              ('systemctl', 'is-active')])

    def test_ss_java_mapped_listener_parses_and_stays_private(self):
        sample = ('LISTEN 0 50 [::ffff:127.0.0.1]:8080 [::]:*\n'
                  'LISTEN 0 151 127.0.0.1:9090 0.0.0.0:*\n')
        with patch.object(network, 'call', return_value=sample):
            self.assertEqual(network.bindings(8080), ['[::ffff:127.0.0.1]:8080'])
            self.assertTrue(network.private_listener(network.bindings(8080)))

    def test_safe_diagnostic_never_leaks_env_secrets(self):
        raw = ('JENKINS_LISTEN_ADDRESS=127.0.0.1 '
               'JENKINS_OPTS=--httpListenAddress=0.0.0.0 '
               'PRIVATE_TOKEN=DO_NOT_EXPOSE_THIS')
        result = network._safe_systemd_diagnostic(lambda *args: raw)
        self.assertIn('contradictoire', result)
        self.assertNotIn('PRIVATE_TOKEN', result)
        self.assertNotIn('DO_NOT_EXPOSE_THIS', result)

    def test_real_conflict_fails_safe_and_restores(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'jenkins.conf'
            # Forbid waiting 90 seconds, simulate timeout after restart.
            ticks = iter([0, 0, 100])
            outputs = []
            def exec_( *args, **kwargs):
                outputs.append(args)
                if 'show' in args:
                    return 'JENKINS_PORT=8080 JENKINS_OPTS=--httpListenAddress=0.0.0.0 JENKINS_LISTEN_ADDRESS=127.0.0.1'
                return ''
            with patch.object(network.time, 'monotonic', side_effect=lambda: next(ticks)):
                with self.assertRaises(network.NetworkError) as cm:
                    network.enforce(8080, approved=True, timeout=10, dropin=path,
                                    read_bindings=lambda _: ['*:8080'], executor=exec_)
            self.assertIn('contradictoire', str(cm.exception))
            self.assertFalse(path.exists())
            self.assertEqual(sum('restart' in args for args in outputs), 2)
            self.assertNotIn('JENKINS_OPTS=', str(cm.exception))


class CLIActivityTests(unittest.TestCase):
    def test_no_loader_before_question_then_loader_while_restarting(self):
        cfg = SimpleNamespace(jenkins_port=8080)
        state = SimpleNamespace(jenkins_installed=True, jenkins_active=True,
                                jenkins_private=False, jenkins_listening=True)
        events = []
        class ActivitySpy:
            def __init__(self, *args, **kwargs):
                events.append(('construct', args[0]))
            def __enter__(self):
                events.append(('begin', None))
                return self
            def __exit__(self, typ, value, tb):
                events.append(('end', None))
        def confirm(*args):
            self.assertEqual([], events, 'do not animate while waiting for human input')
            return True
        def perform(*args, **kwargs):
            self.assertIn(('begin', None), events)
            return 'fixed'
        with patch.object(cli, 'yesno', side_effect=confirm), \
             patch.object(cli, 'Activity', ActivitySpy), \
             patch.object(cli, 'enforce', side_effect=perform), \
             patch.object(cli, 'section'), \
             redirect_stdout(io.StringIO()):
            cli.apply_stage('services', state, cfg)
        self.assertEqual(events[-1], ('end', None))

    def test_no_animation_if_confirmation_declined(self):
        cfg = SimpleNamespace(jenkins_port=8080)
        state = SimpleNamespace(jenkins_installed=True, jenkins_active=True,
                                jenkins_private=False, jenkins_listening=True)
        with patch.object(cli, 'yesno', return_value=False), \
             patch.object(cli, 'Activity', side_effect=AssertionError('unexpected spinner')), \
             patch.object(cli, 'enforce', side_effect=AssertionError('unexpected restart')), \
             patch.object(cli, 'section'), redirect_stdout(io.StringIO()):
            with self.assertRaises(cli.CLIError):
                cli.apply_stage('services', state, cfg)

    def test_git_update_spinner_and_output_do_not_interleave(self):
        result = subprocess.CompletedProcess(args=[], returncode=0,
                                             stdout='Already up to date.\n', stderr='')
        events = []
        class ActivitySpy:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                events.append('start')
            def __exit__(self, *args):
                events.append('stop')
        def run(*args, **kwargs):
            self.assertEqual(events, ['start'])
            self.assertTrue(kwargs['capture_output'])
            self.assertEqual(kwargs['timeout'], 180)
            return result
        with patch.object(cli.os, 'geteuid', return_value=0), \
             patch.object(cli, 'ROOT', ROOT), \
             patch.object(cli, 'command', return_value=subprocess.CompletedProcess([],0,stdout='')), \
             patch.object(cli, 'Activity', ActivitySpy), \
             patch.object(cli.subprocess, 'run', side_effect=run), \
             patch.object(Path, 'exists', return_value=True), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['update']), 0)
        self.assertEqual(events, ['start', 'stop'])
        self.assertIn('Already up to date.', output.getvalue())


if __name__ == '__main__':
    unittest.main()
