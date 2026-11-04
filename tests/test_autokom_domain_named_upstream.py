"""Safe detection of simple, local NGINX upstreams during .tech -> .mg migration."""
from __future__ import annotations
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_domain_migration.py'
spec = importlib.util.spec_from_file_location('autokom_domain_migration', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Deliberately mirrors the DIA site shown in the server-side diagnostic.
DIA_CONF = '''# Configuration générée automatiquement par setup-proxy.sh
# Domaine : dia.ik0m.tech -> 127.0.0.1:8464
upstream dia_ik0m_tech_backend {
    server 127.0.0.1:8464;
    keepalive 64;
}
server {
    server_name dia.ik0m.tech;
    access_log /var/log/nginx/dia.ik0m.tech.access.log;
    error_log /var/log/nginx/dia.ik0m.tech.error.log;
    location / {
        proxy_pass http://dia_ik0m_tech_backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/dia.ik0m.tech/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dia.ik0m.tech/privkey.pem;
}
server {
    if ($host = dia.ik0m.tech) {
        return 301 https://$host$request_uri;
    }
    listen 80;
    server_name dia.ik0m.tech;
    return 404;
}
'''

class NamedUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.nginx_patch = patch.object(m, 'NGINX_DIRS', (self.folder,))
        self.nginx_patch.start()
        self.addCleanup(self.nginx_patch.stop)
        self.ns_patch=patch.object(m, 'authoritative_ns_ok', return_value=(True, ['kallie.ns.cloudflare.com.', 'justin.ns.cloudflare.com.']))
        self.ns_patch.start()
        self.addCleanup(self.ns_patch.stop)

    def discovered(self, content=DIA_CONF):
        (self.folder/'dia.conf').write_text(content, encoding='utf-8')
        return m.discover_nginx()['dia.ik0m.tech']

    def test_exact_dia_configuration_is_eligible(self):
        site=self.discovered()
        self.assertTrue(site['supported'])
        self.assertEqual(site['port'], 8464)
        self.assertEqual(site['proxy_kind'], 'upstream')
        self.assertEqual(len(site['sources']), 1)  # two server_name lines, one file
        self.assertEqual(m.audit_reasons(site), [])

    def test_audit_is_read_only_and_reports_real_port(self):
        self.discovered()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(m.main(['migrate','--source','dia.ik0m.tech','--audit']),0)
        self.assertIn('prêt (proxy local)', output.getvalue())
        self.assertIn('127.0.0.1:8464 (upstream nommé)', output.getvalue())

    def test_second_upstream_server_rejected(self):
        conf=DIA_CONF.replace('keepalive 64;', 'server 127.0.0.1:8465;\n keepalive 64;')
        self.assertFalse(self.discovered(conf)['supported'])

    def test_nonlocal_upstream_server_rejected(self):
        self.assertFalse(self.discovered(DIA_CONF.replace('server 127.0.0.1:8464;', 'server 10.0.1.8:8464;'))['supported'])

    def test_upstream_directive_not_explicitly_allowed_rejected(self):
        self.assertFalse(self.discovered(DIA_CONF.replace('keepalive 64;', 'keepalive 64;\n least_conn;'))['supported'])

    def test_wrong_upstream_name_rejected(self):
        self.assertFalse(self.discovered(DIA_CONF.replace('proxy_pass http://dia_ik0m_tech_backend;', 'proxy_pass http://other;'))['supported'])

    def test_second_location_rejected(self):
        self.assertFalse(self.discovered(DIA_CONF.replace('location / {', 'location /admin { proxy_pass http://dia_ik0m_tech_backend; }\n location / {'))['supported'])

    def test_dynamic_proxy_pass_rejected(self):
        self.assertFalse(self.discovered(DIA_CONF.replace('proxy_pass http://dia_ik0m_tech_backend;', 'proxy_pass http://$request_uri;'))['supported'])

    def test_external_dns_host_rejected(self):
        self.assertFalse(self.discovered(DIA_CONF.replace('server 127.0.0.1:8464;', 'server myserver.example:8464;'))['supported'])

    def test_destination_collision_is_refused_before_dns_update(self):
        self.discovered()
        (self.folder/'existing.conf').write_text(
            'server { server_name dia.ikom.mg; location / { return 200; } }')
        with self.assertRaisesRegex(m.MigrationError, 'déjà défini'):
            m._assert_destination_unclaimed('dia.ikom.mg')

    def test_destination_wildcard_collision_is_refused(self):
        self.discovered()
        (self.folder/'existing.conf').write_text(
            'server { server_name *.ikom.mg; location / { return 200; } }')
        with self.assertRaisesRegex(m.MigrationError, 'déjà défini'):
            m._assert_destination_unclaimed('dia.ikom.mg')

    def test_safe_destination_does_not_collide(self):
        self.discovered()
        m._assert_destination_unclaimed('dia.ikom.mg')

    def test_keep_legacy_single_direct_port(self):
        conf='server {\n server_name dia.ik0m.tech;\n location / { proxy_pass http://127.0.0.1:8464; }\n}'
        site=self.discovered(conf)
        self.assertTrue(site['supported'])
        self.assertEqual(site['port'],8464)

    def test_does_not_enable_static_site(self):
        site=self.discovered(DIA_CONF.replace('proxy_pass http://dia_ik0m_tech_backend;', 'root /var/www/html;'))
        self.assertFalse(site['supported'])

if __name__=='__main__': unittest.main()
