#!/usr/bin/env python3
"""Regressions v13 : validations DNS dès chaque entrée, aucune requête réelle."""
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as obs
from domain_validation import DomainRoutingError


class TestDomainOnEntry(unittest.TestCase):
    def test_bad_dns_retry_then_valid_without_attempt_counter(self):
        with patch.object(obs, 'prompt_text', side_effect=['prometheus.bad.test', 'prometheus.good.test']), \
             patch.object(obs, 'check_direct_ec2_domain', side_effect=[
                 DomainRoutingError('DNS A=8.8.4.4, IP publique EC2=8.8.8.8 : corriger DNS'),
                 ['8.8.8.8']]) as check, \
             patch.object(obs, 'out') as messages:
            domain = obs.prompt_domain('Domaine Prometheus', expected_ipv4='8.8.8.8')
        self.assertEqual(domain, 'prometheus.good.test')
        self.assertEqual(check.call_count, 2)
        self.assertTrue(all(c.kwargs['expected_ipv4'] == '8.8.8.8' for c in check.call_args_list))
        self.assertIn('corriger DNS', str(messages.call_args_list))
        self.assertNotIn('1/3', str(messages.call_args_list))
        self.assertNotIn('2/3', str(messages.call_args_list))

    def test_syntax_failure_does_not_call_dns(self):
        with patch.object(obs, 'prompt_text', side_effect=['https://bad.test', 'valid.example.org']), \
             patch.object(obs, 'check_direct_ec2_domain', return_value=['8.8.8.8']) as check, \
             patch.object(obs, 'out'):
            self.assertEqual(obs.prompt_domain('Domaine Grafana'), 'valid.example.org')
            check.assert_called_once_with('valid.example.org', expected_ipv4=None)

    def test_three_bad_domains_stop_before_asking_grafana(self):
        args = SimpleNamespace(non_interactive=False, prometheus_domain='',
                               grafana_domain='', public_ip=None, email='')
        with patch.object(obs, 'yesno', return_value=True) as choice, \
             patch.object(obs, 'prompt_text', side_effect=['bad.example.net']*3) as prompt, \
             patch.object(obs, 'check_direct_ec2_domain', side_effect=DomainRoutingError('DNS pointe ailleurs')) as dns, \
             patch.object(obs, 'existing_vhosts') as nginx, \
             patch.object(obs, 'out') as messages:
            with self.assertRaisesRegex(obs.SetupError, 'trois saisies'):
                obs.interactive_options(args)
        self.assertEqual(choice.call_count, 1)
        self.assertEqual(prompt.call_count, 3)
        self.assertEqual(dns.call_count, 3)
        nginx.assert_not_called()
        self.assertEqual(args.grafana_domain, '')
        self.assertNotIn('essai(s) restant(s)', str(messages.call_args_list))

    def test_prometheus_validated_before_grafana_question(self):
        args = SimpleNamespace(non_interactive=False, prometheus_domain='',
                               grafana_domain='', public_ip='8.8.8.8', email='admin@example.org')
        events = []
        def ask_choice(question, *a, **kw):
            events.append('menu:' + question)
            return True
        def dns(domain, **kw):
            events.append('dns:' + domain)
            return ['8.8.8.8']
        with patch.object(obs, 'yesno', side_effect=ask_choice), \
             patch.object(obs, 'prompt_text', side_effect=['prom.example.org', 'graf.example.org']), \
             patch.object(obs, 'check_direct_ec2_domain', side_effect=dns), \
             patch.object(obs, 'out'):
            obs.interactive_options(args)
        self.assertEqual(args.prometheus_domain, 'prom.example.org')
        self.assertEqual(args.grafana_domain, 'graf.example.org')
        self.assertLess(events.index('dns:prom.example.org'),
                        events.index('menu:Grafana dispose-t-il d’un domaine HTTPS ?'))

    def test_duplicate_service_domain_not_accepted(self):
        with patch.object(obs, 'prompt_text', side_effect=['prom.example.org', 'graf.example.org']), \
             patch.object(obs, 'check_direct_ec2_domain', return_value=['8.8.8.8']) as check, \
             patch.object(obs, 'out') as out:
            self.assertEqual(obs.prompt_domain('Domaine Grafana', unavailable={'prom.example.org'}),
                             'graf.example.org')
        check.assert_called_once_with('graf.example.org', expected_ipv4=None)
        self.assertIn('déjà réservé', str(out.call_args_list))

    def test_final_preflight_still_detects_dns_drift(self):
        args = SimpleNamespace(prometheus_domain='prom.example.org', grafana_domain='', public_ip=None)
        with patch.object(obs, 'check_direct_ec2_domain', side_effect=DomainRoutingError('DNS changé')):
            with self.assertRaisesRegex(obs.SetupError, 'DNS changé'):
                obs.preflight_domains(args)

    def test_cancel_propagates_without_fabricated_retries(self):
        with patch.object(obs, 'prompt_text', side_effect=KeyboardInterrupt), \
             patch.object(obs, 'check_direct_ec2_domain') as dns:
            with self.assertRaises(KeyboardInterrupt):
                obs.prompt_domain('Domaine Grafana')
        dns.assert_not_called()


if __name__ == '__main__':
    unittest.main()
