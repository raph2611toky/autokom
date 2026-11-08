"""Customer publication ACME: exact real failure HTTP 403, no privileged path exposure.

No external network, Jenkins, Cloudflare, Certbot or real Nginx calls.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import stat
import unittest
from unittest.mock import patch

import autokom_platform_broker as broker

HOST = 'portfolio.auto.ikom.mg'
ENV = '674be29f-4fe2-4d68-b1c9-554e561b8923'


class TestCustomerAcme(unittest.TestCase):
    def test_same_public_root_as_frontend_and_monitoring(self):
        import autokom_frontend_manager as front
        import observability
        self.assertEqual(broker.WEBROOT, Path('/var/www/autokom-acme'))
        self.assertEqual(broker.WEBROOT, front.ACME_WEBROOT)
        self.assertEqual(broker.WEBROOT, observability.ACME_WEBROOT)
        for tls in (False, True):
            config = broker._nginx_config(HOST, 32001, tls=tls)
            self.assertIn('root /var/www/autokom-acme;', config)
            self.assertIn('location ^~ /.well-known/acme-challenge/', config)
            self.assertNotIn('root /var/lib/autokom/acme;', config)

    def test_prepare_public_challenge_directory_with_umask_077(self):
        with TemporaryDirectory() as temp, patch.object(broker, 'WEBROOT', Path(temp)/'acme'):
            old_umask = os.umask(0o077)
            try:
                broker._prepare_public_acme_webroot()
            finally:
                os.umask(old_umask)
            for directory in (broker.WEBROOT, broker.WEBROOT/'.well-known',
                              broker.WEBROOT/'.well-known'/'acme-challenge'):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
            self.assertFalse((Path(temp)/'private-django').exists())

    def test_protect_symlink_and_world_writable_directory(self):
        with TemporaryDirectory() as temp, patch.object(broker, 'WEBROOT', Path(temp)/'acme'):
            (Path(temp)/'acme').symlink_to(Path(temp))
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_ACME_WEBROOT_INVALID'):
                broker._prepare_public_acme_webroot()
        with TemporaryDirectory() as temp, patch.object(broker, 'WEBROOT', Path(temp)/'acme'):
            broker.WEBROOT.mkdir(mode=0o777)
            broker.WEBROOT.chmod(0o777)
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_ACME_WEBROOT_INVALID'):
                broker._prepare_public_acme_webroot()

    def test_preflight_serves_content_via_correct_host_on_private_nic(self):
        with TemporaryDirectory() as temp, patch.object(broker, 'WEBROOT', Path(temp)/'acme'):
            broker._prepare_public_acme_webroot()
            class FakeResponse:
                status = 200
                def read(self, limit):
                    return (next((broker.WEBROOT/'.well-known'/'acme-challenge').iterdir()).read_bytes())
            class FakeConnection:
                def __init__(self, address, port, timeout=None):
                    self.asserted = (address,port,timeout)
                    self.headers = None
                    connections.append(self)
                def request(self, method, uri, headers):
                    self.headers = headers
                    self.method, self.uri = method, uri
                    self.file = next((broker.WEBROOT/'.well-known'/'acme-challenge').iterdir())
                    self.mode = stat.S_IMODE(self.file.stat().st_mode)
                def getresponse(self):
                    return FakeResponse()
                def close(self):
                    pass
            connections = []
            with patch.object(broker, '_acme_ingress_local_ipv4', return_value='10.128.0.4'), \
                 patch.object(broker.http.client, 'HTTPConnection', FakeConnection):
                broker._preflight_public_acme_challenge(HOST)
            self.assertEqual(connections[0].asserted[:2],('10.128.0.4',80))
            self.assertEqual(connections[0].headers['Host'], HOST)
            self.assertEqual(connections[0].method, 'GET')
            self.assertEqual(connections[0].mode, 0o644)
            self.assertFalse(list((broker.WEBROOT/'.well-known'/'acme-challenge').iterdir()))

    def test_preflight_denied_blocks_certbot_and_cleans_up(self):
        with TemporaryDirectory() as temp, patch.object(broker,'WEBROOT',Path(temp)/'acme'):
            broker._prepare_public_acme_webroot()
            class FakeConnection:
                def __init__(self, *args, **kwargs): pass
                def request(self, *args, **kwargs): pass
                def getresponse(self): return SimpleNamespace(status=403,read=lambda limit: b'')
                def close(self): pass
            with patch.object(broker.http.client, 'HTTPConnection', FakeConnection), \
                 patch.object(broker, '_acme_ingress_local_ipv4', return_value='10.128.0.4'):
                with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_ACME_HTTP01_PREFLIGHT_FAILED.*HTTP 403'):
                    broker._preflight_public_acme_challenge(HOST)
            self.assertFalse(list((broker.WEBROOT/'.well-known'/'acme-challenge').iterdir()))

    def test_certbot_uses_shared_root_and_rolls_back_on_preflight_failure(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('available','enabled','state'):
                (root/name).mkdir()
            fake_calls=[]
            def fake_run(argv, **kwargs):
                fake_calls.append((argv,kwargs))
                return ''
            patches=[patch.object(broker,'PROXY',root/'available'),
                     patch.object(broker,'ENABLED',root/'enabled'),
                     patch.object(broker,'STATE',root/'state'),
                     patch.object(broker,'WEBROOT',root/'acme'),
                     patch.object(broker,'_load_state',return_value={'port':32001}),
                     patch.object(broker,'_runtime_dns_env',return_value={'AUTOKOM_ACME_EMAIL':'ops@ikom.mg'}),
                     patch.object(broker,'run',side_effect=fake_run),
                     patch.object(broker,'_preflight_public_acme_challenge'),
                     patch.object(broker,'_verify_customer_https'),
                     patch.object(broker,'_usable_customer_certificate',side_effect=[False,False,True,False])]
            for item in patches:
                item.start()
                self.addCleanup(item.stop)
            result=broker._proxy_apply(HOST,ENV)
            self.assertTrue(result['active'])
            cert_calls=[(cmd,kwargs) for cmd,kwargs in fake_calls if cmd[0]=='certbot']
            self.assertEqual(len(cert_calls), 1)
            # Certbot itself makes HTTP-01 files world-readable (0644);
            # do not weaken the broker's restrictive process umask for private keys.
            self.assertNotIn('umask',cert_calls[0][1])
            self.assertEqual(cert_calls[0][0][cert_calls[0][0].index('-w')+1],str(root/'acme'))
            self.assertFalse(any('umask' in kwargs for cmd,kwargs in fake_calls if cmd[0]!='certbot'))
            with patch.object(broker,'_preflight_public_acme_challenge',side_effect=broker.BrokerError('AUTOKOM_ACME_HTTP01_PREFLIGHT_FAILED')):
                previous=len(fake_calls)
                with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_ACME_HTTP01_PREFLIGHT_FAILED'):
                    broker._proxy_apply('other.auto.ikom.mg',ENV)
                self.assertFalse(any(cmd[0]=='certbot' for cmd,kw in fake_calls[previous:]))
                self.assertFalse((root/'available'/'autokom-customer-other.auto.ikom.mg.conf').exists())

    def test_runner_keeps_restrictive_systemd_umask(self):
        with patch.object(broker.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout='OK')) as caller:
            result=broker.run(['certbot','--version'])
        self.assertEqual(result,'OK')
        self.assertNotIn('umask',caller.call_args.kwargs)
        self.assertNotIn('shell',caller.call_args.kwargs)

if __name__ == '__main__':
    unittest.main()
