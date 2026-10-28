#!/usr/bin/env python3
"""v15: optional ACME contact, one decision per run, safe Certbot argument paths."""
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as o


class OptionalAcmeEmail(unittest.TestCase):
    def test_blank_is_accepted_and_remembered_for_both_services(self):
        args = SimpleNamespace(mode='apply', email='', non_interactive=False)
        with patch.object(o, 'prompt_text', return_value='  ') as ask, \
             patch.object(o, 'existing_vhosts', return_value=[]), \
             patch.object(o, 'valid_cert', return_value=False), \
             patch.object(o, 'out') as out:
            o._certificate_email_for(args, 'prom.example.org')
            o._certificate_email_for(args, 'graf.example.org')
        self.assertEqual(args.email, '')
        self.assertEqual(ask.call_count, 1)
        self.assertIn('Sans e-mail', str(out.call_args_list))

    def test_supplied_email_is_reused_without_prompt(self):
        args = SimpleNamespace(mode='apply', email='admin@example.org')
        with patch.object(o, 'prompt_text') as ask, patch.object(o, 'existing_vhosts') as vhost:
            o._certificate_email_for(args, 'prom.example.org')
            o._certificate_email_for(args, 'graf.example.org')
        ask.assert_not_called()
        vhost.assert_not_called()
        self.assertTrue(args._cert_email_decided)

    def test_check_never_prompts(self):
        args = SimpleNamespace(mode='check', email='')
        with patch.object(o, 'prompt_text') as ask, patch.object(o, 'existing_vhosts') as vh:
            o._certificate_email_for(args, 'prom.example.org')
        ask.assert_not_called()
        vh.assert_not_called()

    def test_already_configured_cert_skips_contact_question(self):
        args = SimpleNamespace(mode='apply', email='')
        with patch.object(o, 'existing_vhosts', return_value=[]), \
             patch.object(o, 'valid_cert', return_value=True), \
             patch.object(o, 'prompt_text') as ask:
            o._certificate_email_for(args, 'prom.example.org')
        ask.assert_not_called()

    def test_invalid_email_can_be_corrected_or_omitted(self):
        args = SimpleNamespace(mode='apply', email='')
        with patch.object(o, 'existing_vhosts', return_value=[]), \
             patch.object(o, 'valid_cert', return_value=False), \
             patch.object(o, 'prompt_text', side_effect=['wrong-at-domain', '']) as ask, \
             patch.object(o, 'out') as output:
            o._certificate_email_for(args, 'prom.example.org')
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(args.email, '')
        self.assertIn('Format d’e-mail invalide', str(output.call_args_list))
        self.assertNotIn('1/3', str(output.call_args_list))

    def test_new_cert_uses_no_email_flag_not_empty_email(self):
        self._cert_args_test('', '--register-unsafely-without-email')

    def test_new_cert_with_email_uses_email_flag(self):
        self._cert_args_test('admin@example.org', '--email')

    def test_existing_certificate_is_reused_without_certbot(self):
        with TemporaryDirectory() as tmp:
            args = SimpleNamespace(email='', mode='apply', non_interactive=True)
            with patch.object(o, 'NGINX_AVAIL', Path(tmp)/'available'), \
                 patch.object(o, 'NGINX_ENABLED', Path(tmp)/'enabled'), \
                 patch.object(o, 'existing_vhosts', return_value=[]), \
                 patch.object(o, 'resolve_domain', return_value=['203.0.113.9']), \
                 patch.object(o, 'valid_cert', return_value=True), \
                 patch.object(o, 'run') as run, \
                 patch.object(o, 'out'), patch.object(o, 'owned_write') as ow:
                (Path(tmp)/'enabled').mkdir()
                o.configure_nginx('prom.example.org', 9090, 'prometheus', args)
            self.assertFalse(any(c.args[0][0] == 'certbot' for c in run.call_args_list))

    def _cert_args_test(self, email, expected_flag):
        with TemporaryDirectory() as tmp:
            available=Path(tmp)/'available'
            enabled=Path(tmp)/'enabled'
            enabled.mkdir()
            args = SimpleNamespace(email=email, mode='apply', non_interactive=True)
            with patch.object(o, 'NGINX_AVAIL', available), \
                 patch.object(o, 'NGINX_ENABLED', enabled), \
                 patch.object(o, 'existing_vhosts', return_value=[]), \
                 patch.object(o, 'resolve_domain', return_value=['203.0.113.9']), \
                 patch.object(o, 'valid_cert', side_effect=[False, True]), \
                 patch.object(o, 'run') as run, patch.object(o, 'out'), \
                 patch.object(o, 'owned_write'):
                o.configure_nginx('prom.example.org', 9090, 'prometheus', args)
            cert_calls=[call.args[0] for call in run.call_args_list if call.args[0][0]=='certbot']
            self.assertEqual(len(cert_calls),1)
            self.assertIn(expected_flag, cert_calls[0])
            self.assertIn('--non-interactive',cert_calls[0])
            self.assertIn('--agree-tos',cert_calls[0])
            self.assertIn('--no-eff-email',cert_calls[0])
            if email:
                self.assertEqual(cert_calls[0][cert_calls[0].index('--email')+1],email)
                self.assertNotIn('--register-unsafely-without-email', cert_calls[0])
            else:
                self.assertNotIn('--email',cert_calls[0])
                self.assertNotIn('',cert_calls[0])


if __name__ == '__main__':
    unittest.main()
