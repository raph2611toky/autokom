"""Tests sans root, sans réseau et sans modifier la production."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_frontend_manager.py'
spec = importlib.util.spec_from_file_location('autokom_frontend_test_module', PATH)
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)


class FrontendManagerTests(unittest.TestCase):
    def test_domain_refusals(self):
        self.assertEqual(fm.domain('Auto.IK0M.tech'), 'auto.ik0m.tech')
        for invalid in ('localhost', 'invalid.local', 'a.test', 'evil;rm -rf /', 'http://host.test'):
            with self.subTest(invalid=invalid), self.assertRaises(fm.FrontendError):
                fm.domain(invalid)

    def test_nginx_reverse_proxy_preserves_api_and_spa(self):
        text = fm.nginx_config('auto.ik0m.tech', 'backend.auto.ik0m.tech', 8172)
        self.assertIn('try_files $uri $uri/ /index.html;', text)
        self.assertIn('location ^~ /api/', text)
        self.assertIn('proxy_pass http://127.0.0.1:8172;', text)
        self.assertIn('proxy_set_header Host backend.auto.ik0m.tech;', text)
        self.assertNotIn('proxy_pass http://56.', text)
        self.assertNotIn('/var/www/', text)
        self.assertEqual(fm.RELEASES, Path('/opt/autokom/frontend/releases'))
        self.assertIn('proxy_no_cache 1;', text)
        self.assertIn('location ^~ /api/internal/ { return 404; }', text)
        self.assertIn('Content-Security-Policy', text)
        self.assertIn('/opt/autokom/frontend/current', text)

    def test_setup_check_no_writes(self):
        args = __import__('argparse').Namespace(domain='auto.ik0m.tech', backend_domain='backend.auto.ik0m.tech',
            tls_mode='existing', email=None, nginx_mode='existing', apply=False, check=True)
        with mock.patch.object(fm, 'ensure_source'), mock.patch.object(fm, 'config_urls',
             return_value=('auto.ik0m.tech','backend.auto.ik0m.tech')), \
             mock.patch.object(fm, 'save_state') as save, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fm.setup(args), 0)
        save.assert_not_called()

    def test_frontend_cli_commands_have_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as exit_:
            fm.main(['deploy', '-h'])
        self.assertEqual(exit_.exception.code, 0)
        self.assertIn('--check', out.getvalue())
        self.assertIn('--apply', out.getvalue())

    def test_release_id_restricted(self):
        for value in ('../../root', '20261104T142200Z-12345678/../', 'abcdef'):
            self.assertIsNone(fm.RELEASE_ID.fullmatch(value))

    def test_default_cookie_auth_env_remains_valid_for_old_env_files(self):
        import types
        import sys
        old = {'VITE_AUTOKOM_API_BASE': '/api',
               'VITE_AUTOKOM_FRONTEND_ONLY': 'false',
               'VITE_AUTOKOM_API_ENABLED': 'true',
               'VITE_AUTOKOM_GITHUB_ENABLED': 'false'}
        env_module = types.SimpleNamespace(read_env=lambda _: old)
        with mock.patch.dict(sys.modules, {'autokom_env_manager': env_module}):
            result = fm.build_environment(fm.FRONTEND)
        self.assertEqual(result['VITE_AUTOKOM_AUTH_MODE'], 'cookie')

    def test_certbot_email_is_optional(self):
        self.assertEqual(fm.certbot_command('auto.ikom.mg', ''), [
            'certbot', '--nginx', '--non-interactive', '--agree-tos', '--redirect',
            '--register-unsafely-without-email', '-d', 'auto.ikom.mg'])
        self.assertEqual(fm.certbot_command('auto.ikom.mg', 'contact@example.org')[-4:],
                         ['--email', 'contact@example.org', '-d', 'auto.ikom.mg'])

    def test_acme_email_prefers_explicit_then_runtime_fallback(self):
        import sys
        import types
        fake = types.SimpleNamespace(read_env=lambda _: {'AUTOKOM_ACME_EMAIL': 'runtime@example.org'})
        with mock.patch.dict(sys.modules, {'autokom_env_manager': fake}):
            self.assertEqual(fm.acme_email('cli@example.org'), 'cli@example.org')
            self.assertEqual(fm.acme_email(None), 'runtime@example.org')
            with self.assertRaises(fm.FrontendError):
                fm.acme_email('bad email')
        fake = types.SimpleNamespace(read_env=lambda _: {})
        with mock.patch.dict(sys.modules, {'autokom_env_manager': fake}):
            self.assertEqual(fm.acme_email(None), '')

    def test_setup_check_accepts_certbot_without_email(self):
        import sys
        import types
        args = __import__('argparse').Namespace(
            domain='auto.ikom.mg', backend_domain='backend.auto.ikom.mg',
            tls_mode='certbot', email=None, nginx_mode='managed', apply=False, check=True)
        fake = types.SimpleNamespace(read_env=lambda _: {})
        with mock.patch.dict(sys.modules, {'autokom_env_manager': fake}), \
             mock.patch.object(fm, 'ensure_source'), \
             mock.patch.object(fm, 'existing_vhost_conflicts'), \
             mock.patch.object(fm, 'save_state') as save, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(fm.setup(args), 0)
        save.assert_not_called()
        self.assertIn('sans e-mail', output.getvalue())

    def test_source_and_lock_consistent(self):
        fm.ensure_source()



# These tests deliberately mock provisioning: the test suite never touches APT.
class FrontendNodeProvisionTests(unittest.TestCase):
    def test_vite7_supported_node_versions(self):
        for version in ('v20.19.0', 'v22.12.0', 'v22.16.0', 'v24.0.0'):
            with self.subTest(version=version):
                self.assertTrue(fm.node_compatible(version))
        for version in ('v18.20.8', 'v20.18.9', 'v21.6.0', 'v22.11.9', 'garbage'):
            with self.subTest(version=version):
                self.assertFalse(fm.node_compatible(version))

    def test_missing_toolchain_detected_on_builder_path(self):
        with mock.patch.object(fm.shutil, 'which', side_effect=lambda command, **kwargs: None):
            self.assertIn('node absent', fm.node_toolchain_issue())

    def test_incompatible_node_version_detected(self):
        from types import SimpleNamespace
        versions = iter(['v18.20.8', '10.5.1'])
        with mock.patch.object(fm.shutil, 'which', return_value='/usr/bin/executable'), \
             mock.patch.object(fm.subprocess, 'run', side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(versions))):
            self.assertIn('incompatible', fm.node_toolchain_issue())

    def test_node_provisioning_skips_when_compatible(self):
        with mock.patch.object(fm, 'require_root'), \
             mock.patch.object(fm, 'node_toolchain_issue', return_value=None), \
             mock.patch.object(fm, 'run') as runner, \
             contextlib.redirect_stdout(io.StringIO()):
            fm.provision_node_toolchain()
        runner.assert_not_called()

    def test_node_provisioning_runs_signed_ansible_playbook_then_verifies(self):
        with mock.patch.object(fm, 'require_root'), \
             mock.patch.object(fm, 'node_toolchain_issue', side_effect=['npm absent', None]), \
             mock.patch.object(fm.shutil, 'which', return_value='/usr/bin/ansible-playbook'), \
             mock.patch.object(fm, 'run') as runner, \
             contextlib.redirect_stdout(io.StringIO()):
            fm.provision_node_toolchain()
        runner.assert_called_once_with(
            ['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(fm.NODE_PLAYBOOK)],
            cwd=fm.ROOT / 'ansible', timeout=1800)

    def test_node_provisioning_fails_closed_if_version_wrong_after_ansible(self):
        with mock.patch.object(fm, 'require_root'), \
             mock.patch.object(fm, 'node_toolchain_issue', side_effect=['node absent', 'node v18 incompatible']), \
             mock.patch.object(fm.shutil, 'which', return_value='/usr/bin/ansible-playbook'), \
             mock.patch.object(fm, 'run'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(fm.FrontendError, 'non opérationnels après provisioning'):
                fm.provision_node_toolchain()

    def test_setup_check_plans_installation_without_mutating(self):
        import argparse
        args = argparse.Namespace(domain='auto.ikom.mg', backend_domain='backend.auto.ikom.mg',
            tls_mode='existing', email=None, nginx_mode='existing', apply=False, check=True)
        with mock.patch.object(fm, 'ensure_source'), \
             mock.patch.object(fm, 'node_toolchain_issue', return_value='node absent'), \
             mock.patch.object(fm, 'provision_node_toolchain') as provision, \
             mock.patch.object(fm, 'save_state') as save, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(fm.setup(args), 0)
        provision.assert_not_called()
        save.assert_not_called()
        self.assertIn('automatiquement', output.getvalue())

    def test_setup_apply_aborts_before_writing_when_provision_fails(self):
        import argparse
        args = argparse.Namespace(domain='auto.ikom.mg', backend_domain='backend.auto.ikom.mg',
            tls_mode='existing', email=None, nginx_mode='existing', apply=True, check=False)
        with mock.patch.object(fm, 'ensure_source'), \
             mock.patch.object(fm, 'require_root'), \
             mock.patch.object(fm.shutil, 'which', return_value='/usr/sbin/nginx'), \
             mock.patch.object(fm, 'provision_node_toolchain', side_effect=fm.FrontendError('APT failed')), \
             mock.patch.object(fm, 'builder_prepare') as builder, \
             mock.patch.object(fm, 'save_state') as save, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(fm.FrontendError, 'APT failed'):
                fm.setup(args)
        builder.assert_not_called()
        save.assert_not_called()

    def test_ansible_role_has_repo_signature_pin_and_no_remote_shell(self):
        role = (fm.ROOT / 'ansible/roles/frontend_node/tasks/main.yml').read_text()
        self.assertIn('6F71F525282841EEDAF851B42F59B5F99B1BE0B4', role)
        self.assertIn('Signed-By:', role)
        self.assertIn('node_22.x', role)
        self.assertIn('Pin-Priority: 700', role)
        self.assertNotIn('curl | bash', role)
        self.assertNotIn('shell:', role)

if __name__ == '__main__':
    unittest.main()

class FrontendDNSProvisionTests(unittest.TestCase):
    """No external DNS calls; mock Cloudflare and the recursive resolver."""

    def test_only_ikom_mg_subdomains_are_eligible(self):
        self.assertEqual(fm.cloudflare_frontend_zone('auto.ikom.mg'), 'ikom.mg')
        self.assertIsNone(fm.cloudflare_frontend_zone('auto.ik0m.tech'))
        self.assertIsNone(fm.cloudflare_frontend_zone('customer.ikom.mg'))
        for reserved in ('ikom.mg', 'ns1.ikom.mg', 'ns2.ikom.mg',
                         'infra.ikom.mg', 'origin.ikom.mg'):
            self.assertIsNone(fm.cloudflare_frontend_zone(reserved))

    def test_ingress_ip_read_from_runtime_env_and_is_public(self):
        import sys
        from types import SimpleNamespace
        for raw in ('56.228.86.160', '127.0.0.1', '10.1.1.1', ''):
            fake = SimpleNamespace(read_env=lambda _: {'AUTOKOM_INGRESS_IPV4': raw})
            with mock.patch.dict(sys.modules, {'autokom_env_manager': fake}):
                if raw == '56.228.86.160':
                    self.assertEqual(fm.frontend_ingress_ip(), raw)
                else:
                    with self.assertRaises(fm.FrontendError):
                        fm.frontend_ingress_ip()

    def test_dns_record_created_only_when_hostname_missing(self):
        import sys
        from types import SimpleNamespace
        cf = mock.Mock()
        cf.records.return_value = []
        fake = SimpleNamespace(Cloudflare=lambda: cf, DNSError=Exception)
        with mock.patch.dict(sys.modules, {'autokom_dns_cloudflare': fake}), \
             mock.patch.object(fm, 'frontend_ingress_ip', return_value='56.228.86.160'), \
             contextlib.redirect_stdout(io.StringIO()):
            result = fm.ensure_frontend_dns('auto.ikom.mg')
        self.assertEqual(result, '56.228.86.160')
        cf.create.assert_called_once_with('ikom.mg', 'A', 'auto.ikom.mg',
                                          '56.228.86.160', ttl=120, proxied=False)

    def test_existing_matching_a_is_not_changed(self):
        import sys
        from types import SimpleNamespace
        cf = mock.Mock()
        cf.records.return_value = [
            {'type': 'A', 'name': 'auto.ikom.mg', 'content': '56.228.86.160', 'proxied': True},
        ]
        fake = SimpleNamespace(Cloudflare=lambda: cf, DNSError=Exception)
        with mock.patch.dict(sys.modules, {'autokom_dns_cloudflare': fake}), \
             mock.patch.object(fm, 'frontend_ingress_ip', return_value='56.228.86.160'), \
             contextlib.redirect_stdout(io.StringIO()):
            result = fm.ensure_frontend_dns('auto.ikom.mg')
        self.assertIsNone(result)
        cf.create.assert_not_called()
        cf.delete.assert_not_called()

    def test_conflicting_dns_is_never_modified(self):
        import sys
        from types import SimpleNamespace
        fake = SimpleNamespace(DNSError=Exception)
        for entries in (
            [{'type':'A','name':'auto.ikom.mg','content':'203.0.113.10'}],
            [{'type':'CNAME','name':'auto.ikom.mg','content':'other.example.org'}],
            [{'type':'AAAA','name':'auto.ikom.mg','content':'2001:db8::1'}],
            [{'type':'A','name':'auto.ikom.mg','content':'56.228.86.160'},
             {'type':'A','name':'auto.ikom.mg','content':'56.228.86.161'}],
        ):
            cf = mock.Mock()
            cf.records.return_value = entries
            fake.Cloudflare = lambda cf=cf: cf
            with mock.patch.dict(sys.modules, {'autokom_dns_cloudflare': fake}), \
                 mock.patch.object(fm, 'frontend_ingress_ip', return_value='56.228.86.160'), \
                 self.assertRaises(fm.FrontendError):
                fm.ensure_frontend_dns('auto.ikom.mg')
            cf.create.assert_not_called()
            cf.delete.assert_not_called()

    def test_dns_timeout_prevents_certbot_and_is_retryable(self):
        import argparse
        args = argparse.Namespace(domain='auto.ikom.mg', backend_domain='backend.auto.ikom.mg',
                                  tls_mode='certbot', email='contact@example.org',
                                  nginx_mode='managed', apply=True, check=False)
        with mock.patch.object(fm, 'ensure_source'), mock.patch.object(fm, 'require_root'), \
             mock.patch.object(fm, 'existing_vhost_conflicts'), \
             mock.patch.object(fm, 'provision_node_toolchain'), \
             mock.patch.object(fm, 'ensure_frontend_dns'), \
             mock.patch.object(fm, 'wait_frontend_dns', side_effect=fm.FrontendError('DNS non visible')), \
             mock.patch.object(fm, 'builder_prepare') as builder, \
             mock.patch.object(fm, 'save_state') as state, \
             mock.patch.object(fm, 'run') as run, \
             mock.patch.object(fm.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(fm, 'STATE', Path('/nonexistent/frontend-state.json')), \
             mock.patch.object(fm, 'VHOST', Path('/nonexistent/frontend.conf')), \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaises(fm.FrontendError):
            fm.setup(args)
        builder.assert_not_called()
        run.assert_not_called()
        state.assert_not_called()

    def test_dns_check_displays_plan_but_does_not_write(self):
        import argparse
        args = argparse.Namespace(domain='auto.ikom.mg', backend_domain='backend.auto.ikom.mg',
                                  tls_mode='certbot', email='contact@example.org',
                                  nginx_mode='managed', apply=False, check=True)
        with mock.patch.object(fm, 'ensure_source'), mock.patch.object(fm, 'existing_vhost_conflicts'), \
             mock.patch.object(fm, 'node_toolchain_issue', return_value=None), \
             mock.patch.object(fm, 'ensure_frontend_dns') as dns, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fm.setup(args), 0)
        dns.assert_not_called()
        self.assertIn('DNS : contrôle/création idempotente', out.getvalue())

    def test_dns_visibility_requires_origin_ip_for_dns_only(self):
        import socket
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.51.100.8', 80)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('56.228.86.160', 80))]
        with mock.patch.object(fm.socket, 'getaddrinfo', return_value=addresses):
            self.assertTrue(fm.dns_ipv4_visible('auto.ikom.mg', '56.228.86.160'))
            self.assertFalse(fm.dns_ipv4_visible('auto.ikom.mg', '56.228.86.161'))
