#!/usr/bin/env python3
"""Offline tests: no root, no APT, no HTTP or filesystem mutations."""
import io
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts' / 'lib'))
import observability_config as c
import observability as o


class DynamicConfigTests(unittest.TestCase):
    def setUp(self):
        self.original = o.SETTINGS
        o.SETTINGS = c.Settings()

    def tearDown(self):
        o.SETTINGS = self.original

    def test_defaults(self):
        settings = c.load(system_path=Path('/not-existing-system-config'))
        self.assertEqual((8080, 9090, 3000), (settings.jenkins_port, settings.prometheus_port, settings.grafana_port))

    def test_precedence_defaults_system_config_and_cli_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = root / 'system.ini'
            override = root / 'extra.ini'
            system.write_text('[prometheus]\nport=9098\n[grafana]\nport=3040\n')
            override.write_text('[prometheus]\nport=9191\n')
            settings = c.load(custom_path=override, system_path=system, changes=['grafana.port=3333'])
            self.assertEqual((9191,3333), (settings.prometheus_port,settings.grafana_port))
            self.assertEqual(settings.prometheus_base, 'http://127.0.0.1:9191')

    def test_custom_ports_flow_to_all_generated_documents(self):
        o.SETTINGS = c.validate(replace(c.Settings(), jenkins_port=8117,
            prometheus_port=9124, grafana_port=3124, scrape_interval='25s',
            prometheus_domain='prom.example.com', grafana_domain='graf.example.com'))
        self.assertIn('127.0.0.1:8117', o.prom_scrape_config())
        self.assertIn("127.0.0.1:9124", o.prom_scrape_config())
        self.assertIn('--web.listen-address=127.0.0.1:9124', o.unit_override_prometheus())
        self.assertIn('--web.external-url=https://prom.example.com/', o.unit_override_prometheus())
        self.assertIn('GF_SERVER_HTTP_PORT=3124', o.unit_override_grafana('graf.example.com'))
        self.assertIn('http://127.0.0.1:9124', o.datasource_yaml())
        self.assertIn('timeInterval: 25s', o.datasource_yaml())
        self.assertIn('proxy_pass http://127.0.0.1:3124;', o.vhost_content('graf.example.com', 3124, gated=False))
        self.assertEqual(o.cfg().jenkins_metrics_url, 'http://127.0.0.1:8117/prometheus/')

    def test_dynamic_http_allowlist(self):
        o.SETTINGS = c.validate(replace(c.Settings(),prometheus_port=9199))
        with self.assertRaises(o.SetupError):
            o.get_local('http://127.0.0.1:9090/-/ready')
        with self.assertRaises(o.SetupError):
            o.get_local('http://0.0.0.0:9199/-/ready')
        with self.assertRaises(o.SetupError):
            o.get_local('http://evil.example.com:9199/-/ready')
        with patch.object(o.urllib.request, 'build_opener') as build:
            build.return_value.open.side_effect = o.urllib.error.URLError('not running')
            code, data = o.get_local('http://127.0.0.1:9199/-/ready')
            self.assertEqual(code, 0)

    def test_reject_public_bind_and_conflicting_ports(self):
        for pair in ['prometheus.host=0.0.0.0', 'grafana.host=0.0.0.0',
                     'jenkins.host=192.168.1.2', 'grafana.port=9090',
                     'prometheus.port=80', 'jenkins.metrics_path=/foo?x=1',
                     'prometheus.storage_path=/tmp/prom', 'prometheus.scrape_interval=zero']:
            with self.subTest(pair=pair), self.assertRaises(c.ConfigError):
                c.load(system_path=Path('/nonexistent'), changes=[pair])

    def test_reject_unknown_variable_and_secret_option(self):
        for pair in ['foo.port=1', 'jenkins.admin_password=secret', 'grafana.admin_password=secret',
                     'prometheus.port', 'badvalue']:
            with self.subTest(pair=pair), self.assertRaises(c.ConfigError):
                c.load(changes=[pair], system_path=Path('/none'))

    def test_show_config_does_not_touch_machine(self):
        with patch.object(o, 'preflight') as preflight, patch.object(o, 'check_only') as check:
            captured=io.StringIO()
            with patch('sys.stdout', captured):
                self.assertEqual(o.main(['--show-config', '--set', 'jenkins.port=8112']), 0)
            self.assertIn('http://127.0.0.1:8112/prometheus/', captured.getvalue())
            preflight.assert_not_called()
            check.assert_not_called()

    def test_configured_nginx_metrics_path(self):
        o.SETTINGS = c.validate(replace(c.Settings(),jenkins_metrics_path='/jenkins-metrics/'))
        text = '''server { listen 443 ssl; server_name ci.example.com; location / { proxy_pass http://127.0.0.1:8080; } }'''
        # parser requires directives at starts of lines; use formatted layout
        text='''server {\nlisten 443 ssl;\nserver_name ci.example.com;\nlocation / { proxy_pass http://127.0.0.1:8080; }\n}\n'''
        hardened, changed = o.jenkins_vhost_patch(text,'ci.example.com')
        self.assertTrue(changed)
        self.assertIn('location ^~ /jenkins-metrics/ { return 403; }', hardened)
        self.assertFalse(o.jenkins_vhost_patch(hardened,'ci.example.com')[1])

    def test_migration_previous_state(self):
        import yaml
        o.SETTINGS = c.validate(replace(c.Settings(),jenkins_port=8090,prometheus_port=9190))
        text='''global:\n  scrape_interval: 30s\nscrape_configs:\n  - job_name: prometheus\n    static_configs:\n      - targets: ['127.0.0.1:9090']\n  - job_name: jenkins\n    metrics_path: /prometheus/\n    static_configs:\n      - targets: ['127.0.0.1:8080']\n  - job_name: custom\n    static_configs:\n      - targets: ['127.0.0.1:8888']\n'''
        with patch.object(o,'previous_state',return_value={}):
            result=yaml.safe_load(o.merge_prom_config(text))
        jobs={j['job_name']:j for j in result['scrape_configs']}
        self.assertEqual(jobs['jenkins']['static_configs'][0]['targets'],['127.0.0.1:8090'])
        self.assertEqual(jobs['prometheus']['static_configs'][0]['targets'],['127.0.0.1:9190'])
        self.assertEqual(jobs['custom']['static_configs'][0]['targets'],['127.0.0.1:8888'])

    def test_no_changes_preserve_prometheus_yaml(self):
        text=o.prom_scrape_config()
        with patch.object(o,'previous_state',return_value={}):
            result=o.merge_prom_config(text)
        self.assertEqual(result,text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
