"""Regression: real domain.activate path must create a private owned vhost.

Offline only: never call Cloudflare, Certbot, Nginx, Docker or systemd.
"""
import logging
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autokom_platform_broker as broker
REAL_RUN = broker.run

DOMAIN='portfolio.auto.ikom.mg'
ENV='674be29f-4fe2-4d68-b1c9-554e561b8923'


class DomainActivationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.available = root / 'available'
        self.enabled = root / 'enabled'
        self.state = root / 'state'
        self.webroot = root / 'acme'
        for folder in (self.available, self.enabled, self.state, self.webroot):
            folder.mkdir()
        for name,value in (
            ('PROXY',self.available),('ENABLED',self.enabled),
            ('STATE',self.state),('WEBROOT',self.webroot),
            ('_load_state', {'port':32001,'environment_id':ENV}),
            ('_check_dns', {'verified':True,'ipv4':'56.228.86.160'}),
            ('_runtime_dns_env', {'AUTOKOM_ACME_EMAIL':'ops@ikom.mg'}),
        ):
            if name.startswith('_'):
                helper = patch.object(broker,name,return_value=value)
            else:
                helper = patch.object(broker,name,value)
            helper.start()
            self.addCleanup(helper.stop)
        self.calls = []
        def fake_run(argv,**kwargs):
            self.calls.append((list(argv),kwargs))
            return ''
        helper=patch.object(broker,'run',side_effect=fake_run)
        helper.start()
        self.addCleanup(helper.stop)

    def test_domain_activate_creates_owned_vhost_without_typeerror(self):
        result=broker.handle('domain.activate',{
            'hostname':DOMAIN,'environment_id':ENV,'managed':True})
        self.assertEqual(result, {'active':True,'hostname':DOMAIN})
        vhost=self.available/('autokom-customer-'+DOMAIN+'.conf')
        link=self.enabled/vhost.name
        owner=self.state/('domain-owner-'+DOMAIN+'.txt')
        self.assertTrue(vhost.is_file())
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(),vhost)
        self.assertEqual(owner.read_text(),ENV)
        config=vhost.read_text()
        self.assertIn('listen 443 ssl;',config)
        self.assertIn('proxy_pass http://127.0.0.1:32001;',config)
        self.assertTrue(any(command[0]=='certbot' for command, kw in self.calls))
        self.assertEqual(sum(command==['nginx','-t'] for command,kw in self.calls), 2)

    def test_activation_idempotent_and_owner_marker_consistent(self):
        for i in range(2):
            broker.handle('domain.activate',{'hostname':DOMAIN,'environment_id':ENV,'managed':True})
        self.assertEqual((self.state/('domain-owner-'+DOMAIN+'.txt')).read_text(),ENV)
        self.assertEqual(len(list(self.enabled.glob('autokom-customer-*'))),1)

    def test_certificate_failure_rolls_back_unpublished_new_vhost(self):
        def fail_certbot(argv,**kwargs):
            if argv[0]=='certbot':
                raise broker.BrokerError('AUTOKOM_CERTBOT_COMMAND_FAILED: Certbot refused.')
            return ''
        with patch.object(broker,'run',side_effect=fail_certbot):
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_ACME_FAILED'):
                broker.handle('domain.activate',{'hostname':DOMAIN,'environment_id':ENV,'managed':True})
        self.assertFalse((self.available/('autokom-customer-'+DOMAIN+'.conf')).exists())
        self.assertFalse((self.enabled/('autokom-customer-'+DOMAIN+'.conf')).is_symlink())
        self.assertFalse((self.state/('domain-owner-'+DOMAIN+'.txt')).exists())

    def test_rejects_domain_owned_by_another_environment(self):
        (self.state/('domain-owner-'+DOMAIN+'.txt')).write_text('11111111-1111-4111-8111-111111111111')
        with self.assertRaisesRegex(broker.BrokerError,'autre environnement'):
            broker.handle('domain.activate',{'hostname':DOMAIN,'environment_id':ENV,'managed':True})

    def test_commands_log_only_safe_category_and_exit_code(self):
        fake=subprocess.CompletedProcess(['nginx','-t','secret-in-args'],1,
                                        stdout='SHOULD_NOT_LOG_STDOUT secret-token',
                                        stderr='SHOULD_NOT_LOG_STDERR another-secret')
        with patch.object(broker.subprocess,'run',return_value=fake):
            with self.assertLogs(broker.logger.name,level='WARNING') as caught:
                with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_NGINX_COMMAND_FAILED'):
                    REAL_RUN(['nginx','-t','secret-in-args'])
        combined='\n'.join(caught.output)
        self.assertIn('component=nginx',combined)
        self.assertIn('exit_status=1',combined)
        for secret in ('secret-token','another-secret','secret-in-args'):
            self.assertNotIn(secret,combined)

    def test_unexpected_error_becomes_diagnostic_code_without_raw_details(self):
        class FakeHandler:
            # inspect the source to confirm the non-BrokerError distinction
            pass
        from inspect import getsource
        code=getsource(broker.Handler.handle)
        self.assertIn('AUTOKOM_BROKER_INTERNAL_ERROR',code)
        self.assertIn('source =',code)
        self.assertNotIn('logger.exception(',code)

    def test_worker_emits_structured_release_and_health_diagnostics(self):
        worker=(Path(__file__).resolve().parents[1]/'platform/backend/apps/deployments/runs/worker.py').read_text()
        self.assertIn('AUTOKOM_TECH:release:Docker ·',worker)
        self.assertIn('AUTOKOM_TECH:health:Domaine public ·',worker)
        self.assertIn('Échec lors de la publication ; code=',worker)
        self.assertIn('safe_code',worker)
        self.assertIn('Retour arrière',worker)


if __name__ == '__main__':
    unittest.main()
