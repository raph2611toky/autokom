#!/usr/bin/env python3
"""V12 - Tests offline, aucun DNS réel/IMDS, sudo ni modification du serveur."""
from __future__ import annotations

import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import observability as obs
import domain_validation as dns


def resolver(*addresses):
    return lambda *args, **kwargs: [
        (2 if ':' not in addr else 10, 1, 6, '', (addr, 80)) for addr in addresses]


class TestListenerSecurity(unittest.TestCase):
    def test_valid_mapped_loopback_in_observability(self):
        for addresses in (['[::ffff:127.0.0.1]:8080'],
                          ['::ffff:127.0.0.1:8080'],
                          ['[::1]:8080'],
                          ['127.0.0.1:8080'],
                          ['127.0.0.1:8080', '[::ffff:127.0.0.1]:8080']):
            with self.subTest(addresses=addresses), patch.object(obs, 'listener', return_value=addresses):
                obs.assert_private(8080)

    def test_public_and_mixed_bind_rejected(self):
        for addresses in (['*:8080'], ['[::]:8080'], ['0.0.0.0:8080'],
                          ['127.0.0.1:8080', '0.0.0.0:8080'],
                          ['[::ffff:0.0.0.0]:8080'], ['172.31.21.188:8080']):
            with self.subTest(addresses=addresses), patch.object(obs, 'listener', return_value=addresses):
                with self.assertRaises(obs.SetupError):
                    obs.assert_private(8080)

    def test_public_jenkins_then_mapped_private_after_restart(self):
        args = SimpleNamespace(mode='apply', non_interactive=False)
        with patch.object(obs, 'active', return_value=True), \
             patch.object(obs, 'listener', side_effect=[['*:8080'], ['[::ffff:127.0.0.1]:8080'], ['[::ffff:127.0.0.1]:8080']]), \
             patch.object(obs, 'yesno', return_value=True), \
             patch.object(obs, 'run') as commands, \
             patch.object(obs, 'owned_write') as write:
            obs.ensure_private_jenkins(args)
            write.assert_called_once()
            self.assertEqual(sum('restart' in call.args[0] for call in commands.call_args_list), 1)

    def test_mapped_listener_skips_restart_and_prompt(self):
        args = SimpleNamespace(mode='apply', non_interactive=False)
        with patch.object(obs, 'active', return_value=True), \
             patch.object(obs, 'listener', return_value=['[::ffff:127.0.0.1]:8080']), \
             patch.object(obs, 'yesno') as ask, patch.object(obs, 'run') as run:
            obs.ensure_private_jenkins(args)
            ask.assert_not_called()
            run.assert_not_called()


class TestDomainDNS(unittest.TestCase):
    def test_correct_realistic_public_ip(self):
        self.assertEqual(dns.check_direct_ec2_domain('grafana.example.org',
            metadata=lambda: '8.8.8.8', resolver=resolver('8.8.8.8'),
            ipv6_addresses=lambda: set()), ['8.8.8.8'])

    def test_wrong_dns_ipv4_rejected(self):
        with self.assertRaisesRegex(dns.DomainRoutingError, 'corriger DNS'):
            dns.check_direct_ec2_domain('grafana.example.org',
                metadata=lambda: '8.8.8.8', resolver=resolver('8.8.4.4'),
                ipv6_addresses=lambda: set())

    def test_mixed_a_records_rejected(self):
        with self.assertRaises(dns.DomainRoutingError):
            dns.check_direct_ec2_domain('grafana.example.org', metadata=lambda: '8.8.8.8',
                resolver=resolver('8.8.8.8', '8.8.4.4'), ipv6_addresses=lambda: set())

    def test_mismatched_aaaa_rejected(self):
        with self.assertRaisesRegex(dns.DomainRoutingError, 'AAAA'):
            dns.check_direct_ec2_domain('grafana.example.org', metadata=lambda: '8.8.8.8',
                resolver=resolver('8.8.8.8', '2606:4700:4700::1111'),
                ipv6_addresses=lambda: set())

    def test_matching_public_ipv6_accepted(self):
        res = dns.check_direct_ec2_domain('grafana.example.org', metadata=lambda: '8.8.8.8',
            resolver=resolver('8.8.8.8', '2606:4700:4700::1111'),
            ipv6_addresses=lambda: {'2606:4700:4700::1111'})
        self.assertEqual(len(res), 2)

    def test_private_dns_rejected(self):
        with self.assertRaises(dns.DomainRoutingError):
            dns.check_direct_ec2_domain('grafana.example.org', metadata=lambda: '8.8.8.8',
                resolver=resolver('127.0.0.1'), ipv6_addresses=lambda: set())

    def test_missing_metadata_fails_closed(self):
        with self.assertRaisesRegex(dns.DomainRoutingError, 'IMDSv2'):
            dns.check_direct_ec2_domain('grafana.example.org', metadata=lambda: None,
                resolver=resolver('8.8.8.8'))

    def test_explicit_ip_fallback_and_mismatch(self):
        self.assertEqual(dns.check_direct_ec2_domain('grafana.example.org',
            expected_ipv4='8.8.8.8', metadata=lambda: None,
            resolver=resolver('8.8.8.8'), ipv6_addresses=lambda: set()), ['8.8.8.8'])
        with self.assertRaises(dns.DomainRoutingError):
            dns.check_direct_ec2_domain('grafana.example.org',
                expected_ipv4='8.8.4.4', metadata=lambda: '8.8.8.8',
                resolver=resolver('8.8.4.4'))
        with self.assertRaises(dns.DomainRoutingError):
            dns.check_direct_ec2_domain('grafana.example.org',
                expected_ipv4='127.0.0.1', metadata=lambda: None,
                resolver=resolver('127.0.0.1'))

    def test_imds_request_fixed_and_proxy_disabled(self):
        class Response:
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return self.value
        opener = Mock()
        opener.open.side_effect = [Response(b'opaque-token'), Response(b'8.8.8.8')]
        with patch.object(dns.urllib.request, 'build_opener', return_value=opener) as factory:
            self.assertEqual(dns.ec2_public_ipv4(), '8.8.8.8')
        self.assertEqual(factory.call_args.args[0].proxies, {})
        req1 = opener.open.call_args_list[0].args[0]
        req2 = opener.open.call_args_list[1].args[0]
        self.assertEqual(req1.get_method(), 'PUT')
        self.assertEqual(req1.full_url, 'http://169.254.169.254/latest/api/token')
        self.assertEqual(req2.full_url, 'http://169.254.169.254/latest/meta-data/public-ipv4')
        self.assertTrue(req2.has_header('X-aws-ec2-metadata-token'))


class TestPreflightFlow(unittest.TestCase):
    def make_args(self, mode='apply'):
        return SimpleNamespace(prometheus_domain='prometheus.example.org',
           grafana_domain='grafana.example.org', public_ip=None, mode=mode,
           non_interactive=False, email='admin@example.org', prometheus_password='',
           prometheus_user='')

    def test_preflight_checks_both_domains(self):
        args = self.make_args()
        with patch.object(obs, 'check_direct_ec2_domain', return_value=['8.8.8.8']) as check, \
             patch.object(obs, 'existing_vhosts', return_value=[]), \
             patch.object(obs, 'check_http01_local') as http:
            obs.preflight_domains(args)
            self.assertEqual(check.call_count, 2)
            http.assert_called_once()

    def test_existing_vhosts_not_need_http_01_preflight(self):
        args = self.make_args()
        with patch.object(obs, 'check_direct_ec2_domain', return_value=['8.8.8.8']), \
             patch.object(obs, 'existing_vhosts', return_value=['existing']) as sites, \
             patch.object(obs, 'check_http01_local') as http:
            obs.preflight_domains(args)
            self.assertEqual(sites.call_count, 2)
            http.assert_not_called()

    def test_dns_failure_before_credentials_install_and_restart(self):
        args = self.make_args()
        with patch.object(obs, 'check_direct_ec2_domain', side_effect=dns.DomainRoutingError('DNS A mauvais')), \
             patch.object(obs, 'interactive_prometheus_password') as secret, \
             patch.object(obs, 'ensure_private_jenkins') as restart:
            with self.assertRaisesRegex(obs.SetupError, 'DNS A mauvais'):
                obs.preflight_domains(args)
            secret.assert_not_called()
            restart.assert_not_called()

    def test_local_http80_probe_rejected(self):
        with self.assertRaises(dns.DomainRoutingError):
            dns.check_http01_local(connector=Mock(side_effect=ConnectionRefusedError()))

    def test_no_domain_no_dns(self):
        args = self.make_args()
        args.prometheus_domain = ''
        args.grafana_domain = ''
        with patch.object(obs, 'check_direct_ec2_domain') as check:
            obs.preflight_domains(args)
            check.assert_not_called()

    def test_domain_prompt_retries_invalid_input(self):
        with patch.object(obs, 'prompt_text', side_effect=['', 'https://invalid.test', 'metrics.example.com']) as prompt, \
             patch.object(obs, 'check_direct_ec2_domain', return_value=['8.8.8.8']):
            self.assertEqual(obs.prompt_domain('Domaine'), 'metrics.example.com')
            self.assertEqual(prompt.call_count, 3)

    def test_domain_prompt_three_errors(self):
        with patch.object(obs, 'prompt_text', side_effect=['', '', '']):
            with self.assertRaisesRegex(obs.SetupError, 'trois saisies'):
                obs.prompt_domain('Domaine')

    def test_no_prometheus_password_prompt_in_check_mode(self):
        args = self.make_args(mode='check')
        with patch.object(obs, 'password_twice') as pwd:
            obs.interactive_prometheus_password(args)
            pwd.assert_not_called()


if __name__ == '__main__':
    unittest.main()
