"""Production publication regression tests; no network, APT, Nginx, Cloudflare or CA calls."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock

import autokom_platform_broker as broker
import observability_dns01 as dns01

HOST = 'portfolio.auto.ikom.mg'
ENV = '674be29f-4fe2-4d68-b1c9-554e561b8923'
REAL_HTTPS_VERIFY = broker._verify_customer_https


class CertificateTests(TestCase):
    def test_cert_reuse_requires_separate_host_and_expiry_checks(self):
        with TemporaryDirectory() as d:
            path = Path(d) / HOST
            path.mkdir()
            (path / 'fullchain.pem').write_text('fake')
            (path / 'privkey.pem').write_text('fake')
            calls = []
            def openssl(argv, **kw):
                calls.append(argv)
                if '-checkhost' in argv:
                    return SimpleNamespace(returncode=1)
                return SimpleNamespace(returncode=0)
            with patch.object(broker.subprocess, 'run', side_effect=openssl):
                self.assertFalse(broker._usable_customer_certificate(HOST, live_root=Path(d)))
            self.assertEqual(len(calls), 1)
            self.assertIn('-checkhost', calls[0])
            calls.clear()
            with patch.object(broker.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as proc:
                self.assertTrue(broker._usable_customer_certificate(HOST, live_root=Path(d)))
            self.assertEqual(proc.call_count, 2)
            self.assertIn('-checkhost', proc.call_args_list[0].args[0])
            self.assertIn('-checkend', proc.call_args_list[1].args[0])

    def test_managed_dns01_requires_plugin_without_installing_from_broker(self):
        with patch.object(broker, '_usable_customer_certificate', return_value=False), \
             patch.object(broker, '_runtime_dns_env', return_value={'AUTOKOM_ACME_EMAIL':'ops@ikom.mg'}), \
             patch.object(broker, '_cloudflare_settings', return_value={'suffix':'auto.ikom.mg','token':'dummy'}), \
             patch.object(dns01, 'plugin_available', return_value=False), \
             patch.object(broker, 'run') as system:
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_ACME_DNS01_PLUGIN_MISSING'):
                broker._issue_customer_certificate(HOST, managed=True)
            system.assert_not_called()

    def test_managed_dns01_uses_private_credentials_no_http_or_apt(self):
        with patch.object(broker, '_usable_customer_certificate', side_effect=[False,True]), \
             patch.object(broker, '_runtime_dns_env', return_value={'AUTOKOM_ACME_EMAIL':'ops@ikom.mg'}), \
             patch.object(broker, '_cloudflare_settings', return_value={'suffix':'auto.ikom.mg','token':'TOPSECRET'}), \
             patch.object(dns01, 'plugin_available', return_value=True), \
             patch.object(dns01, 'ensure_certbot_credentials', return_value=Path('/etc/letsencrypt/autokom/cloudflare-dns.ini')) as creds, \
             patch.object(broker, 'run') as system:
            result=broker._issue_customer_certificate(HOST, managed=True)
            self.assertEqual(result, 'dns01')
            creds.assert_called_once_with('TOPSECRET')
            args=system.call_args.args[0]
            self.assertIn('--dns-cloudflare', args)
            self.assertIn('--cert-name', args)
            self.assertIn(HOST, args)
            self.assertNotIn('--webroot', args)
            self.assertNotIn('TOPSECRET', ' '.join(args))
            self.assertNotIn('apt-get', args)

    def test_valid_cert_skips_certbot_and_dns_plugin(self):
        with patch.object(broker, '_usable_customer_certificate', return_value=True), \
             patch.object(broker, '_cloudflare_settings') as cloud, \
             patch.object(broker, 'run') as system:
            self.assertEqual(broker._issue_customer_certificate(HOST, managed=True), 'reused')
            cloud.assert_not_called()
            system.assert_not_called()


class PublishTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for folder in ('enabled','available','state'):
            (root/folder).mkdir()
        for name, value in [('ENABLED',root/'enabled'),('PROXY',root/'available'),('STATE',root/'state')]:
            p=patch.object(broker,name,value)
            p.start(); self.addCleanup(p.stop)
        for name,kw in [('_load_state', {'return_value': {'port':32001,'healthcheck':'/health'}}),
                        ('_usable_customer_certificate', {'return_value':False}),
                        ('_issue_customer_certificate', {'return_value':'dns01'}),
                        ('_verify_customer_https', {})]:
            p=patch.object(broker,name,**kw)
            p.start(); self.addCleanup(p.stop)
        p=patch.object(broker,'run')
        p.start(); self.addCleanup(p.stop)

    def test_managed_domain_does_not_stage_http_when_dns01(self):
        with patch.object(broker, '_preflight_public_acme_challenge') as http:
            result=broker._proxy_apply(HOST, ENV, managed=True)
        self.assertTrue(result['active'])
        http.assert_not_called()
        conf=(broker.PROXY/('autokom-customer-'+HOST+'.conf')).read_text()
        self.assertIn('proxy_pass http://127.0.0.1:32001;',conf)
        self.assertIn('listen 443 ssl;',conf)
        self.assertIn('ssl_certificate /etc/letsencrypt/live/'+HOST+'/fullchain.pem;',conf)
        self.assertTrue((broker.ENABLED/('autokom-customer-'+HOST+'.conf')).is_symlink())

    def test_managed_existing_cert_skips_issue(self):
        with patch.object(broker,'_usable_customer_certificate', return_value=True), \
             patch.object(broker, '_issue_customer_certificate') as issuer:
            broker._proxy_apply(HOST,ENV,managed=True)
        issuer.assert_not_called()

    def test_https_verify_failure_restores_prior_known_vhost(self):
        conf=broker.PROXY/('autokom-customer-'+HOST+'.conf')
        old='server { listen 443 ssl; server_name '+HOST+'; }\n'
        conf.write_text(old)
        (broker.ENABLED/conf.name).symlink_to(conf)
        (broker.STATE/('domain-owner-'+HOST+'.txt')).write_text(ENV)
        with patch.object(broker, '_verify_customer_https',side_effect=broker.BrokerError('AUTOKOM_HTTPS_SNI_FAILED')):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HTTPS_SNI_FAILED'):
                broker._proxy_apply(HOST,ENV,managed=True)
        self.assertEqual(conf.read_text(),old)
        self.assertTrue((broker.ENABLED/conf.name).is_symlink())
        self.assertEqual((broker.STATE/('domain-owner-'+HOST+'.txt')).read_text(),ENV)

    def test_https_verify_failure_does_not_leave_invalid_new_tls_vhost(self):
        with patch.object(broker, '_verify_customer_https',side_effect=broker.BrokerError('AUTOKOM_HTTPS_SNI_FAILED')):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HTTPS_SNI_FAILED'):
                broker._proxy_apply(HOST,ENV,managed=True)
        self.assertFalse((broker.PROXY/('autokom-customer-'+HOST+'.conf')).exists())
        self.assertFalse((broker.ENABLED/('autokom-customer-'+HOST+'.conf')).is_symlink())
        self.assertFalse((broker.STATE/('domain-owner-'+HOST+'.txt')).exists())

    def test_https_socket_checks_sni_and_health_path(self):
        raw=MagicMock()
        tls=MagicMock()
        raw.__enter__.return_value=raw
        tls.__enter__.return_value=tls
        context=MagicMock()
        context.wrap_socket.return_value=tls
        response=MagicMock()
        response.status=200
        with patch.object(broker.ssl, 'create_default_context', return_value=context), \
             patch.object(broker.socket, 'create_connection', return_value=raw) as connect, \
             patch.object(broker.http.client, 'HTTPResponse', return_value=response):
            REAL_HTTPS_VERIFY(HOST, '/health')
        connect.assert_called_once_with(('127.0.0.1',443),timeout=6)
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], HOST)
        self.assertIn(b'GET /health HTTP/1.1',tls.sendall.call_args.args[0])

    def test_https_http_503_blocks_activation(self):
        raw=MagicMock(); tls=MagicMock(); context=MagicMock()
        raw.__enter__.return_value=raw; tls.__enter__.return_value=tls
        context.wrap_socket.return_value=tls
        response=MagicMock(); response.status=503
        with patch.object(broker.ssl,'create_default_context',return_value=context), \
             patch.object(broker.socket,'create_connection',return_value=raw), \
             patch.object(broker.http.client,'HTTPResponse',return_value=response):
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_HTTPS_UPSTREAM_FAILED.*503'):
                REAL_HTTPS_VERIFY(HOST,'/')


class RecoverDnsPluginTests(TestCase):
    def test_recovery_admin_provisions_plugin_before_customer_publication(self):
        import autokom_recover as recovery
        import autokom_control_recovery as control
        import observability_dns01
        import autokom_frontend_manager as front
        from autokom_runtime_integrity import Integrity
        with TemporaryDirectory() as temp:
            cfg = Path(temp) / 'cloudflare-dns.env'
            cfg.write_text('owned-by-test')
            with patch.object(recovery, 'logging_target',return_value=('systemd','loki')), \
                 patch.object(recovery,'frontend_targets',return_value=('auto.ikom.mg','backend.auto.ikom.mg')), \
                 patch('autokom_app_manager.verify_health',return_value=True), \
                 patch('autokom_runtime_integrity.inspect',return_value=Integrity(True,True,True,True,True)), \
                 patch('autokom_jenkins_pipeline.inspect',return_value=SimpleNamespace(ready=True)), \
                 patch.object(control,'inspect',return_value=control.Health('active','active','ok',True)), \
                 patch('logging_stack.install_complete',return_value=True), \
                 patch.object(recovery,'ready_frontend',return_value=True), \
                 patch.object(observability_dns01,'CLOUDFLARE_CONFIG',cfg), \
                 patch.object(observability_dns01,'plugin_available',side_effect=[False,True]), \
                 patch.object(observability_dns01,'ensure_dns_plugin') as install, \
                 patch('os.geteuid',return_value=0):
                self.assertEqual(recovery.run(apply=True),0)
            install.assert_called_once()
            self.assertTrue(callable(install.call_args.args[0]))
