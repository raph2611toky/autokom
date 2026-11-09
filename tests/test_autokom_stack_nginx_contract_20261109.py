"""Isolated checks: unchanged legacy Nginx, opt-in gateway, and no cross-account proxy."""
import pathlib
import sys
import types
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'lib'
sys.path.insert(0, str(LIB))
if 'autokom_nginx_bind_policy' not in sys.modules:
    # Snapshot is built without the host-specific optional bind policy module.
    policy = types.ModuleType('autokom_nginx_bind_policy')
    policy.https_listens = lambda: '    listen 127.0.0.1:443 ssl;\n'
    policy.tls_detected = lambda source: 'ssl_certificate' in source
    policy.addresses = lambda: ['127.0.0.1']
    policy.BindPolicyError = type('BindPolicyError', (Exception,), {})
    sys.modules[policy.__name__] = policy

import autokom_platform_broker as broker

class TestStackBroker(unittest.TestCase):
    def test_frontend_routes_backend_only_when_explicitly_configured(self):
        legacy = broker._nginx_config('portfolio.auto.ikom.mg', 20500, tls=True)
        frontend = broker._nginx_config('portfolio.auto.ikom.mg', 20500, tls=True, gateway_port=20501)
        self.assertNotIn('__autokom_backend', legacy)
        self.assertIn('location ^~ /__autokom_backend/', frontend)
        self.assertIn('proxy_pass http://127.0.0.1:20501/;', frontend)
        self.assertIn('server_name portfolio.auto.ikom.mg;', frontend)
        self.assertIn('proxy_set_header Host backend.portfolio.auto.ikom.mg;', frontend)
        self.assertIn('proxy_pass http://127.0.0.1:20500;', frontend)
        self.assertIn('ssl_certificate /etc/letsencrypt/live/portfolio.auto.ikom.mg/fullchain.pem;',frontend)

    def test_custom_backend_hostname_is_used_only_in_opt_in_gateway(self):
        output = broker._nginx_config('site.example.com', 20500, tls=True,
                                      gateway_port=20501, gateway_hostname='api.example.com')
        self.assertIn('proxy_set_header Host api.example.com;', output)
        self.assertNotIn('backend.site.example.com', output)
        with self.assertRaises(broker.BrokerError):
            broker._nginx_config('site.example.com', 20500, tls=True,
                                 gateway_port=20501, gateway_hostname='bad-domain\nHost: localhost')

    def test_reject_bad_gateway(self):
        with self.assertRaises(broker.BrokerError):
            broker._nginx_config('portfolio.auto.ikom.mg', 20500, tls=True, gateway_port=80)

    def test_backend_public_domain_is_valid(self):
        self.assertEqual('backend.portfolio.auto.ikom.mg', broker.valid_hostname('backend.portfolio.auto.ikom.mg'))

if __name__ == '__main__': unittest.main()
