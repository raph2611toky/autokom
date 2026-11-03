"""Regression tests for AUTOKOM DNS; no external network, no system changes."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts/lib'))
sys.path.insert(0, str(BASE / 'scripts/dev'))

import autokom_dns_core as core
import autokom_dns as dns
import autokom_dns_cloudflare as cfmod
from activate_dns_cli import patch as cli_patch

class TestDomainValidation(unittest.TestCase):
    def test_zone_cannot_use_cloudflare_root(self):
        for zone in ('ikom.mg', 'google.com', '../../etc/shadow', 'bad_infra.ikom.mg'):
            with self.assertRaises(core.DNSError): core.validate_zone(zone)
        self.assertEqual(core.validate_zone('infra.ikom.mg'), 'infra.ikom.mg')

    def test_ipv4_ipv6_validation(self):
        self.assertEqual(core.normalize_record('infra.ikom.mg','A','api','192.0.2.3')['value'], '192.0.2.3')
        self.assertEqual(core.normalize_record('infra.ikom.mg','AAAA','api','2001:db8::1')['value'], '2001:db8::1')
        with self.assertRaises(core.DNSError):
            core.normalize_record('infra.ikom.mg','A','api','something')

    def test_txt_mail_and_zone_security(self):
        rec = core.normalize_record('infra.ikom.mg','TXT','_dmarc','v=DMARC1; p=reject')
        self.assertEqual(rec['name'], '_dmarc.infra.ikom.mg.')
        with self.assertRaises(core.DNSError):
            core.normalize_record('infra.ikom.mg','TXT','@','bad\nIN TXT evil')
        with self.assertRaises(core.DNSError):
            core.normalize_record('infra.ikom.mg','TXT','_dmarc.elsewhere.net','hello')
        with self.assertRaises(core.DNSError):
            core.normalize_record('infra.ikom.mg','TXT','@','x'*256)

    def test_mx_priority_required(self):
        with self.assertRaises(core.DNSError):
            core.normalize_record('infra.ikom.mg','MX','@','mail.ikom.mg')
        self.assertEqual(core.normalize_record('infra.ikom.mg','MX','@','mail.ikom.mg', priority=10)['priority'], 10)

    def test_cname_conflict(self):
        state = {'serial': 1,'records':[]}
        core.add_record(state, core.normalize_record('infra.ikom.mg','A','a','192.0.2.1'))
        with self.assertRaises(core.DNSError):
            core.add_record(state, core.normalize_record('infra.ikom.mg','CNAME','a','b.infra.ikom.mg'))
        self.assertEqual(len(state['records']), 1)

    def test_zone_soas_serial_increments_and_ns(self):
        state = {'zone':'infra.ikom.mg', 'ns1':'ns1.ikom.mg','ns2':'ns2.ikom.mg',
                 'serial':2026110301,'records':[]}
        core.bump_serial(state)
        self.assertGreater(state['serial'], 2026110301)
        data = core.zone_text(state)
        self.assertIn('IN SOA ns1.ikom.mg.', data)
        self.assertIn('IN NS ns2.ikom.mg.', data)

    def test_named_conf_include_once(self):
        with patch.object(core, 'MANAGED_INCLUDE', Path('/random/missing/autokom.conf')):
            with patch.object(core, 'LOCAL_INCLUDE', Path('/random/missing/local.conf')):
                x, y = core.ensure_includes('infra.ikom.mg')
                self.assertIn('include "/etc/bind/autokom/zones/infra.ikom.mg.conf";', x)
                self.assertIn('include "/random/missing/autokom.conf";', y)

class TestNamedOptions(unittest.TestCase):
    def test_stock_options_recursion_disabled_listen_private(self):
        stock = 'options {\n directory "/var/cache/bind";\n dnssec-validation auto;\n listen-on-v6 { any; };\n};\n'
        new = dns.bound_options(stock, '172.31.10.3')
        self.assertIn('recursion no;', new)
        self.assertIn('listen-on port 53 { 127.0.0.1; 172.31.10.3; };', new)
        self.assertNotIn('listen-on-v6 { any; }', new)
        self.assertEqual(dns.bound_options(new, '172.31.10.3'), new)

    def test_reject_existing_custom_recursion(self):
        with self.assertRaises(core.DNSError):
            dns.bound_options('options { recursion yes; };', '172.31.10.3')
        with self.assertRaises(core.DNSError):
            dns.bound_options('options { listen-on { any; }; };', '172.31.10.3')

class TestCloudflare(unittest.TestCase):
    def test_credentials_not_cli(self):
        with self.assertRaises(core.DNSError):
            cfmod.Cloudflare({'CLOUDFLARE_API_TOKEN':'', 'CLOUDFLARE_ZONE_ID':'abc'})

    def test_idempotent_record_creation(self):
        client = cfmod.Cloudflare({'CLOUDFLARE_API_TOKEN':'fake', 'CLOUDFLARE_ZONE_ID':'123'})
        record = {'id':'123456','name':'ns1.ikom.mg','type':'A','content':'56.228.86.160'}
        with patch.object(client, 'records', return_value=[record]):
            with patch.object(client, 'request') as req:
                obj = client.create('ikom.mg','A','ns1','56.228.86.160')
        self.assertTrue(obj['_autokom_unchanged'])
        req.assert_not_called()

class TestCLI(unittest.TestCase):
    def test_parser_and_actions(self):
        args = dns.parse(['setup','--zone','infra.ikom.mg','--role','primary','--check'])
        self.assertEqual(args.role, 'primary')
        self.assertTrue(args.check)
        args = dns.parse(['record','add','--zone','infra.ikom.mg','--type','A','--name','api','--value','192.0.2.1'])
        self.assertEqual(args.type, 'A')

    def test_safe_cli_patcher_idempotent(self):
        old = '''def make_parser():\n    subcommand('app', help='service')\n\ndef main(argv=None):\n    if arguments:\n        cmd=arguments[0]\n        mutating=(cmd in ('update',))\n    if arguments and arguments[0] == 'app':\n        pass\n'''
        updated = cli_patch(old)
        self.assertIn("subcommand('dns'", updated)
        self.assertIn("from autokom_dns import main", updated)
        self.assertEqual(cli_patch(updated), updated)
        self.assertIn('dns_sensitive', updated)

    def test_guard_existing_custom_cli(self):
        with self.assertRaises(RuntimeError): cli_patch('invalid file')

if __name__ == '__main__': unittest.main()
