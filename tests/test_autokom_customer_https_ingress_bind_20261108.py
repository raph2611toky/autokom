"""Regression: SNI must be probed on the production ingress NIC, not VPN/loopback."""
import os
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import autokom_platform_broker as broker

HOST = 'portfolio.auto.ikom.mg'


class HTTPSBindProbeTests(unittest.TestCase):
    def test_configured_private_ingress_is_authoritative(self):
        with patch.object(broker, 'https_bind_addresses', return_value=('10.128.0.4', '127.0.0.1')):
            self.assertEqual(broker._customer_https_probe_address(), '10.128.0.4')

    def test_no_policy_keeps_legacy_loopback(self):
        with patch.object(broker, 'https_bind_addresses', return_value=()):
            self.assertEqual(broker._customer_https_probe_address(), '127.0.0.1')

    def test_invalid_policy_fails_closed(self):
        with patch.object(broker, 'https_bind_addresses', side_effect=broker.BindPolicyError('bad')):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HTTPS_BIND_POLICY_INVALID'):
                broker._customer_https_probe_address()

    def test_verify_uses_private_ingress_and_validates_sni_and_certificate(self):
        raw, tls, context, response = (MagicMock() for _ in range(4))
        raw.__enter__.return_value = raw
        tls.__enter__.return_value = tls
        context.wrap_socket.return_value = tls
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        response.status = 200
        with patch.object(broker, 'https_bind_addresses', return_value=('10.128.0.4','127.0.0.1')), \
             patch.object(broker.ssl, 'create_default_context', return_value=context) as make_context, \
             patch.object(broker.socket, 'create_connection', return_value=raw) as connect, \
             patch.object(broker.http.client, 'HTTPResponse', return_value=response):
            broker._verify_customer_https(HOST, '/health')
        make_context.assert_called_once_with()
        connect.assert_called_once_with(('10.128.0.4',443), timeout=6)
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], HOST)
        self.assertIn(b'GET /health HTTP/1.1', tls.sendall.call_args.args[0])

    def test_loopback_success_never_masks_failed_ingress(self):
        def probe(host, path, address):
            if address == '10.128.0.4':
                raise ConnectionRefusedError(111, 'connection refused')
            return 200
        with patch.object(broker, 'https_bind_addresses', return_value=('10.128.0.4','127.0.0.1')), \
             patch.object(broker, '_customer_https_probe_once', side_effect=probe) as func, \
             patch.object(broker.time, 'sleep'):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HTTPS_SNI_FAILED.*listener-refused'):
                broker._verify_customer_https(HOST, '/')
        self.assertEqual(func.call_count, 6) # 5 on ingress; loopback diagnostic once
        self.assertEqual(func.call_args.args[2], '127.0.0.1')

    def test_invalid_ca_does_not_retry_or_trust_loopback(self):
        problem = ssl.SSLCertVerificationError(1, 'certificate verify failed')
        with patch.object(broker, 'https_bind_addresses', return_value=('10.128.0.4','127.0.0.1')), \
             patch.object(broker, '_customer_https_probe_once', side_effect=problem) as func, \
             patch.object(broker.time, 'sleep') as sleep:
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HTTPS_CERT_INVALID'):
                broker._verify_customer_https(HOST, '/')
        self.assertEqual(func.call_count, 2)  # ingress then diagnostic-only loopback
        sleep.assert_not_called()

    def test_transient_tls_failure_can_recover(self):
        with patch.object(broker, 'https_bind_addresses', return_value=('10.128.0.4','127.0.0.1')), \
             patch.object(broker, '_customer_https_probe_once', side_effect=[ssl.SSLError(1,'temporary'), 200]) as probe, \
             patch.object(broker.time, 'sleep') as sleep:
            broker._verify_customer_https(HOST, '/')
        self.assertEqual(probe.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_real_upstream_502_remains_blocking(self):
        with patch.object(broker, 'https_bind_addresses', return_value=('10.128.0.4','127.0.0.1')), \
             patch.object(broker, '_customer_https_probe_once', return_value=502) as probe, \
             patch.object(broker.time, 'sleep'):
            with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_HTTPS_UPSTREAM_FAILED.*502'):
                broker._verify_customer_https(HOST, '/')
        self.assertEqual(probe.call_count, 5)

    def test_fixed_error_category_does_not_dump_tls_exception(self):
        error = ssl.SSLError(1, 'TLSV1_UNRECOGNIZED_NAME')
        error.reason = 'TLSV1_UNRECOGNIZED_NAME'
        self.assertEqual(broker._customer_https_failure_category(error), 'sni-unrecognized')
        self.assertEqual(broker._customer_https_failure_category(TimeoutError()), 'connect-timeout')
        self.assertEqual(broker._customer_https_failure_category(ConnectionRefusedError()), 'listener-refused')

if __name__ == '__main__':
    unittest.main()
