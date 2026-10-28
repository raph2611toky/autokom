#!/usr/bin/env python3
"""Offline regression tests for AUTOKOM observation module (no root/network)."""
import importlib.util
import io
import sys
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts" / "lib"))
spec = importlib.util.spec_from_file_location('observability', HERE/'scripts/lib/observability.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class TestObservability(unittest.TestCase):
    def test_domain_valid(self):
        self.assertEqual(o.validate_domain('PROM.Example.COM.'), 'prom.example.com')
        for item in ('http://example.com', '127.0.0.1', 'bad_value.net', '*.example.com', 'bad..com', 'example.com/a'):
            with self.subTest(item=item), self.assertRaises(o.SetupError):
                o.validate_domain(item)

    def test_private_bind(self):
        with patch.object(o, 'listener', return_value=['127.0.0.1:9090']):
            o.assert_private(9090)
        with patch.object(o, 'listener', return_value=['0.0.0.0:9090']):
            with self.assertRaises(o.SetupError):
                o.assert_private(9090)
        with patch.object(o, 'listener', return_value=['[::]:9090']):
            with self.assertRaises(o.SetupError):
                o.assert_private(9090)

    def test_grafana_only_localhost(self):
        config = o.unit_override_grafana('grafana.example.com')
        self.assertIn('GF_SERVER_HTTP_ADDR=127.0.0.1', config)
        self.assertIn('GF_SERVER_ROOT_URL=https://grafana.example.com/', config)
        self.assertNotIn('GF_SECURITY_ADMIN_PASSWORD', config)

    def test_prometheus_only_localhost(self):
        self.assertIn('--web.listen-address=127.0.0.1:9090', o.unit_override_prometheus())
        self.assertNotIn('--web.enable-lifecycle', o.unit_override_prometheus())
        self.assertIn('metrics_path: /prometheus/', o.prom_scrape_config())
        self.assertIn("'127.0.0.1:8080'", o.prom_scrape_config())

    def test_scrape_auth_has_password_file_not_password(self):
        config = o.prom_scrape_config('scraper', Path('/etc/prometheus/autokom-jenkins-token'))
        self.assertIn('password_file:', config)
        self.assertNotIn('password: supersecret', config)

    def test_datasource_loopback(self):
        self.assertIn('http://127.0.0.1:9090', o.datasource_yaml())
        self.assertNotIn('basicAuthPassword', o.datasource_yaml())

    def test_nginx_gated_http(self):
        text = o.vhost_content('prom.example.com', 9090, prometheus=True)
        self.assertIn('return 503;', text)
        self.assertNotIn('auth_basic_user_file', text)  # no password challenge over HTTP
        self.assertNotIn('proxy_pass', text)
        result = o.vhost_content('prom.example.com', 9090, prometheus=True, gated=False)
        self.assertIn('proxy_pass http://autokom_prometheus_9090;', result)
        self.assertIn('auth_basic_user_file', result)
        self.assertIn('listen 443 ssl;', result)

    def test_no_old_site_overwrite(self):
        args = MagicMock(mode='apply')
        with patch.object(o, 'existing_vhosts', return_value=['proxy_pass http://127.0.0.1:8080;']):
            with self.assertRaises(o.SetupError):
                o.configure_nginx('grafana.example.com', 3000, 'grafana', args)

    def test_no_dns_when_no_domain(self):
        with patch.object(o, 'existing_vhosts') as sites:
            o.configure_nginx('', 3000, 'grafana', MagicMock())
            sites.assert_not_called()

    def test_secrets_not_in_args(self):
        names = str(o.ROOT)
        self.assertNotIn('Grafana admin password=', o.unit_override_grafana(''))

    def test_cli_parser_syntax(self):
        with patch.object(o, 'interactive_options'), patch.object(o, 'preflight'), patch.object(o, 'check_only') as c, patch.object(o.os, 'geteuid', return_value=0):
            self.assertEqual(0, o.main(['--check', '--non-interactive']))
            c.assert_called_once()

    def test_check_never_applies(self):
        with patch.object(o, 'interactive_options'), patch.object(o, 'preflight'), patch.object(o, 'check_only'), patch.object(o, 'ensure_prometheus') as ep, patch.object(o, 'ensure_grafana') as eg:
            o.main(['--check', '--non-interactive'])
            ep.assert_not_called()
            eg.assert_not_called()


class TestHardening(unittest.TestCase):
    JENKINS_CONF = """
upstream jenkins_ik0m_tech_backend {
  server 127.0.0.1:8080;
}
server {
  server_name jenkins.ik0m.tech;
  location / {
    proxy_pass http://jenkins_ik0m_tech_backend;
  }
  listen 443 ssl;
  ssl_certificate /etc/letsencrypt/live/jenkins.ik0m.tech/fullchain.pem;
}
server {
  listen 80;
  server_name jenkins.ik0m.tech;
  return 301 https://$host$request_uri;
}
"""

    def test_existing_jenkins_vhost_harden_once(self):
        result, did_change = o.jenkins_vhost_patch(self.JENKINS_CONF, 'jenkins.ik0m.tech')
        self.assertTrue(did_change)
        self.assertEqual(result.count('location ^~ /prometheus/'), 1)
        self.assertLess(result.index('location ^~ /prometheus/'), result.index('location / {'))
        result_again, did_change_again = o.jenkins_vhost_patch(result, 'jenkins.ik0m.tech')
        self.assertFalse(did_change_again)
        self.assertEqual(result_again, result)

    def test_harden_jenkins_nonmatching_domain_fails(self):
        with self.assertRaises(o.SetupError):
            o.jenkins_vhost_patch(self.JENKINS_CONF, 'other.example.com')

    def test_nginx_443_only_for_app(self):
        config = o.vhost_content('grafana.example.com', 3000, gated=False)
        self.assertIn('return 301 https://$host$request_uri;', config)
        self.assertEqual(config.count('proxy_pass'), 2)  # Default + Grafana Live WebSocket
        self.assertNotIn('auth_basic_user_file', config)

    def test_auth_deny_if_public_jenkins(self):
        with patch.object(o, 'listener', return_value=['*:8080']), patch.object(o, 'active', return_value=True):
            with patch.object(o, 'yesno', return_value=False):
                with self.assertRaises(o.SetupError):
                    o.ensure_private_jenkins(MagicMock(mode='apply', non_interactive=False))

    def test_new_grafana_password_uses_api_body_not_cli(self):
        replies = [(200, '{}'), (200, '{}'), (200, '{}')]
        args = MagicMock(mode='apply', grafana_user='admin', non_interactive=False)
        with patch.object(o, 'password_twice', return_value='PasswordSafeAtLeast16'), \
             patch.object(o, 'grafana_api', side_effect=replies) as api:
            o.grafana_credentials(True, args)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(api.call_args_list[1].args[3], 'PUT')
        self.assertEqual(api.call_args_list[1].args[4]['newPassword'], 'PasswordSafeAtLeast16')

    def test_prometheus_port_remains_private(self):
        with patch.object(o, 'listener', return_value=['127.0.0.1:9090']):
            o.assert_private(9090)

    def test_domain_collision_detection(self):
        with self.assertRaises((o.SetupError, o.ConfigError)):
            o.main(['--check','--non-interactive','--prometheus-domain','same.example.com',
                    '--grafana-domain','same.example.com'])

    def test_disable_live_without_tty(self):
        p = MagicMock(returncode=0, stdout='ok', stderr='')
        with patch.object(o.subprocess, 'run', return_value=p) as r:
            self.assertEqual(o.run(['printf','ok']), 'ok')
            self.assertEqual(r.call_count, 1)



if __name__ == '__main__':
    unittest.main(verbosity=2)
