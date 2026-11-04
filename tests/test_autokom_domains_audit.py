"""Read-only audit and fail-closed behavior for complex Nginx vhosts."""
from __future__ import annotations
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE=Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_domain_migration.py'
spec=importlib.util.spec_from_file_location('autokom_domain_migration',MODULE)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class DomainAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site=Path(self.tmp.name)
        self.settings=patch.object(m,'NGINX_DIRS',(self.site,))
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.ns=patch.object(m,'authoritative_ns_ok',return_value=(True,['justin.ns.cloudflare.com.','kallie.ns.cloudflare.com.']))
        self.ns.start()
        self.addCleanup(self.ns.stop)

    def conf(self,content):
        (self.site/'site.conf').write_text(content)
        return m.discover_nginx()

    def test_simple_local_proxy_remains_eligible(self):
        d=self.conf('server {\n server_name dia.ik0m.tech;\n location / { proxy_pass http://127.0.0.1:8089; }\n}\n')
        self.assertTrue(d['dia.ik0m.tech']['supported'])
        self.assertEqual(m.audit_reasons(d['dia.ik0m.tech']),[])

    def test_static_site_does_not_become_eligible(self):
        d=self.conf('server {\n server_name dia.ik0m.tech;\n root /var/www/test;\n}\n')
        self.assertFalse(d['dia.ik0m.tech']['supported'])
        self.assertIn('directives root/alias (contenu statique ou ACME spécifique)',m.audit_reasons(d['dia.ik0m.tech']))

    def test_docker_upstream_explains_reason(self):
        d=self.conf('server {\n server_name dia.ik0m.tech;\n location / { proxy_pass http://backend:8000; }\n}\n')
        self.assertFalse(d['dia.ik0m.tech']['supported'])
        self.assertTrue(any('proxy non local' in x for x in m.audit_reasons(d['dia.ik0m.tech'])))

    def test_audit_flag_is_read_only(self):
        self.conf('server {\n server_name dia.ik0m.tech;\n root /srv/static;\n}\n')
        s=io.StringIO()
        with contextlib.redirect_stdout(s):
            rc=m.main(['migrate','--source','dia.ik0m.tech','--audit'])
        self.assertEqual(rc,0)
        self.assertIn('directives root/alias',s.getvalue())

    def test_apply_with_all_unsupported_returns_without_external_access(self):
        self.conf('server {\n server_name dia.ik0m.tech;\n root /srv/static;\n}\n')
        s=io.StringIO()
        with patch.object(m,'cloudflare',side_effect=AssertionError('no API allowed')):
            with contextlib.redirect_stdout(s):
                rc=m.main(['migrate','--apply'])
        self.assertEqual(rc,2)
        self.assertIn('aucun vhost',s.getvalue().lower())

    def test_destination_rejects_dns_critical(self):
        with self.assertRaises(m.MigrationError):
            m.destination('ns1.ik0m.tech')

if __name__=='__main__':
    unittest.main()
