"""Staged AWS-only DNS deployment and Ansible role regression tests."""
import argparse
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from contextlib import redirect_stdout

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts/lib'))
import autokom_dns as dns
import autokom_dns_core as core


def setup_args(**kw):
    options = dict(role='primary', zone='infra.ikom.mg', environment='production',
                   primary_ip='56.228.86.160', secondary_ip=None, ns1='ns1.ikom.mg',
                   ns2='ns2.ikom.mg', listen_ip='172.31.20.44', check=True,
                   apply=False, yes=False)
    options.update(kw)
    return argparse.Namespace(**options)


class TestStagedPrimary(unittest.TestCase):
    def test_single_ns_zone_and_transfer_disabled(self):
        state = dns._zone_state('infra.ikom.mg', 'production', 'primary',
                                '56.228.86.160', None, 'ns1.ikom.mg', 'ns2.ikom.mg')
        state['serial'] = 2026110301
        zone = core.zone_text(state)
        self.assertIn('IN NS ns1.ikom.mg.', zone)
        self.assertNotIn('IN NS ns2.ikom.mg.', zone)
        conf = core.render_bind_zone_statement(state)
        self.assertIn('allow-transfer { none; };', conf)
        self.assertIn('notify no;', conf)
        self.assertNotIn('also-notify', conf)

    def test_secondary_must_have_ip(self):
        with self.assertRaises(core.DNSError):
            dns._zone_state('infra.ikom.mg', 'production', 'secondary',
                            '56.228.86.160', None, 'ns1.ikom.mg', 'ns2.ikom.mg')

    def test_primary_without_gcp_ip_dryrun(self):
        with tempfile.TemporaryDirectory() as d:
            statefile = Path(d) / 'state.json'
            with patch.object(core, 'state_path', return_value=statefile), \
                 patch.object(dns, 'local_listen_ips', return_value=['172.31.20.44']), \
                 patch('sys.stdin.isatty', return_value=False):
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(dns.bind_setup(setup_args()), 0)
            self.assertIn('primaire seul', stdout.getvalue())
            self.assertFalse(statefile.exists())

    def test_upgrade_with_gcp_keeps_records_and_bumps_serial(self):
        with tempfile.TemporaryDirectory() as d:
            statefile = Path(d) / 'state.json'
            prev = dns._zone_state('infra.ikom.mg', 'production', 'primary',
                                   '56.228.86.160', None, 'ns1.ikom.mg', 'ns2.ikom.mg')
            prev['serial'] = 2026110301
            prev['records'] = [core.normalize_record('infra.ikom.mg', 'A', 'api', '192.0.2.1')]
            import json
            statefile.write_text(json.dumps(prev))
            with patch.object(core, 'state_path', return_value=statefile), \
                 patch.object(dns, 'local_listen_ips', return_value=['172.31.20.44']), \
                 patch('sys.stdin.isatty', return_value=False), \
                 patch.object(core, 'bump_serial', wraps=core.bump_serial) as bump:
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(dns.bind_setup(setup_args(secondary_ip='203.0.113.20')), 0)
            self.assertIn('ajout secondaire GCP', stdout.getvalue())
            self.assertEqual(bump.call_count, 1)
            self.assertEqual(prev, json.loads(statefile.read_text()))

    def test_reject_changed_existing_secondary(self):
        with tempfile.TemporaryDirectory() as d:
            statefile = Path(d) / 'state.json'
            prev = dns._zone_state('infra.ikom.mg', 'production', 'primary',
                                   '56.228.86.160', '203.0.113.20', 'ns1.ikom.mg', 'ns2.ikom.mg')
            import json
            statefile.write_text(json.dumps(prev))
            with patch.object(core, 'state_path', return_value=statefile), \
                 patch.object(dns, 'local_listen_ips', return_value=['172.31.20.44']), \
                 patch('sys.stdin.isatty', return_value=False):
                with self.assertRaises(core.DNSError):
                    dns.bind_setup(setup_args(secondary_ip='203.0.113.50'))

    def test_verify_skips_missing_gcp(self):
        state = dns._zone_state('infra.ikom.mg', 'production', 'primary',
                                '56.228.86.160', None, 'ns1.ikom.mg', 'ns2.ikom.mg')
        run_result = Mock(returncode=0, stdout='infra.ikom.mg. SOA ns1.ikom.mg.')
        with patch.object(core, 'load_state', return_value=state), \
             patch.object(dns.shutil, 'which', return_value='/usr/bin/dig'), \
             patch.object(dns, 'run', return_value=run_result) as run:
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(dns.verify(argparse.Namespace(zone='infra.ikom.mg')), 0)
        self.assertEqual(run.call_count, 2)
        self.assertIn('non configuré', stdout.getvalue())


class TestAnsibleRoles(unittest.TestCase):
    def test_role_resolves_from_playbook_directory(self):
        play = BASE / 'ansible/playbooks/dns.yml'
        role = BASE / 'ansible/roles/dns_server'
        self.assertTrue((role / 'tasks/main.yml').is_file())
        self.assertIn('role: ../roles/dns_server', play.read_text())
        self.assertEqual((play.parent / '../roles/dns_server').resolve(), role.resolve())

    def test_check_validates_syntax_and_never_installs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'ansible/playbooks').mkdir(parents=True)
            (root/'ansible/roles/dns_server/tasks').mkdir(parents=True)
            (root/'ansible/inventories/local').mkdir(parents=True)
            (root/'ansible/playbooks/dns.yml').write_text('---\n')
            (root/'ansible/roles/dns_server/tasks/main.yml').write_text('---\n')
            (root/'ansible/inventories/local/hosts.ini').write_text('[local]\nlocalhost ansible_connection=local\n')
            with patch.object(dns, 'ROOT', root), \
                 patch.object(dns.shutil, 'which', return_value='/usr/bin/ansible-playbook'), \
                 patch.object(dns.subprocess, 'run', return_value=Mock(returncode=0, stdout='ok', stderr='')) as proc:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(dns.install(argparse.Namespace(check=True,apply=False,yes=False)), 0)
            self.assertEqual(proc.call_count,1)
            args, kwargs = proc.call_args
            self.assertEqual(args[0][-1], '--syntax-check')
            self.assertIn(str(root / 'ansible/roles'),kwargs['env']['ANSIBLE_ROLES_PATH'])


if __name__ == '__main__':
    unittest.main()
