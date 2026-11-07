"""Public reverse-proxy ingress should be provisioned before Certbot, idempotently."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import firewall
from tests.test_firewall import FakeExec


class PublicWebIngressTests(unittest.TestCase):
    def setUp(self):
        self.tools = patch.object(firewall.shutil, 'which',
                                  side_effect=lambda cmd: '/usr/sbin/' + cmd)
        self.tools.start()
        self.addCleanup(self.tools.stop)

    def test_gcp_active_ufw_opens_80_443_only_on_public_nic(self):
        fake = FakeExec(active=True)
        fake.added.add(('allow', 'in', 'on', 'wg0', 'to', 'any', 'port', '443', 'proto', 'tcp'))
        firewall.secure_firewall('mysql', apply=True, executor=fake,
                                 public_web_ports=(80, 443), say=lambda *_: None)
        for port in (80, 443):
            self.assertIn(tuple(firewall.rule_command('allow', 'enp39s0', port)), fake.calls)
        self.assertIn(('allow', 'in', 'on', 'wg0', 'to', 'any', 'port', '443', 'proto', 'tcp'), fake.added)
        self.assertIn(tuple(firewall.rule_command('deny', 'enp39s0', 8080)), fake.calls)
        self.assertIn(tuple(firewall.rule_command('deny', 'enp39s0', 3306)), fake.calls)

    def test_idempotence_no_repeat_insert_for_web_ports(self):
        fake = FakeExec(active=True)
        opts = dict(apply=True, executor=fake, public_web_ports=(80,443), say=lambda *_: None)
        firewall.secure_firewall('mysql', **opts)
        count = len(fake.calls)
        firewall.secure_firewall('mysql', **opts)
        self.assertFalse(any(call[:3] == ('ufw','insert','1') for call in fake.calls[count:]))

    def test_no_public_domain_never_adds_web_allow(self):
        fake = FakeExec(active=True)
        firewall.secure_firewall('mysql', apply=True, executor=fake, say=lambda *_: None)
        self.assertFalse(any(call[:4] == ('ufw','insert','1','allow') and
                             call[4:7] == ('in','on','enp39s0') and
                             call[10] in ('80','443') for call in fake.calls))

    def test_http_site_only_opens_80(self):
        fake = FakeExec(active=True)
        firewall.secure_firewall('none', apply=True, executor=fake,
                                 public_web_ports=(80,), say=lambda *_: None)
        self.assertIn(tuple(firewall.rule_command('allow', 'enp39s0',80)),fake.calls)
        self.assertNotIn(tuple(firewall.rule_command('allow', 'enp39s0',443)),fake.calls)

    def test_existing_wan_deny_not_silently_overridden(self):
        fake = FakeExec(active=True)
        fake.added.add(('deny','in','on','enp39s0','to','any','port','80','proto','tcp'))
        with self.assertRaisesRegex(RuntimeError, 'explicitement bloqué'):
            firewall.secure_firewall('none', apply=True, executor=fake,
                                     public_web_ports=(80,443), say=lambda *_: None)
        self.assertNotIn(tuple(firewall.rule_command('allow','enp39s0',80)),fake.calls)

    def test_check_is_readonly_and_reports_web_ports(self):
        fake = FakeExec(active=True)
        result = firewall.secure_firewall('none', apply=False, executor=fake,
                                          public_web_ports=(80,443), say=lambda *_: None)
        self.assertEqual(result['public_web_ports'],(80,443))
        self.assertFalse(any(call[:3]==('ufw','insert','1') for call in fake.calls))

    def test_cannot_open_8080_or_3306_as_public(self):
        fake = FakeExec(active=True)
        for bad in (8080, 3306, '80', True, 22):
            with self.assertRaises(ValueError):
                firewall.secure_firewall('none', apply=True, executor=fake,
                                         public_web_ports=(bad,), say=lambda *_: None)

    def test_inactive_firewall_requires_ssh_safety_before_opening(self):
        fake = FakeExec(active=False)
        with self.assertRaisesRegex(RuntimeError, 'UFW inactif'):
            firewall.secure_firewall('none', apply=True, executor=fake,
                                     public_web_ports=(80,443), say=lambda *_: None)
        self.assertNotIn(('ufw','--force','enable'),fake.calls)
        self.assertNotIn(tuple(firewall.rule_command('allow','enp39s0',80)),fake.calls)

    def test_service_module_passes_public_web_port_selection(self):
        content = (ROOT / 'scripts' / 'lib' / 'services.py').read_text()
        self.assertIn("('certbot', 'provided', 'existing')", content)
        self.assertIn('public_web_ports=public_web_ports', content)
        self.assertIn('firewall:host', content)
        self.assertLess(content.find('secure_firewall(\n        args.db'),
                        content.find('configure_proxy(args)', content.find('def main():')))

if __name__ == '__main__':
    unittest.main()
