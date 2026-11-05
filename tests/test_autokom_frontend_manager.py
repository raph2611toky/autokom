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
             mock.patch.object(fm, 'run_live') as runner, \
             contextlib.redirect_stdout(io.StringIO()):
            fm.provision_node_toolchain()
        runner.assert_called_once_with(
            ['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(fm.NODE_PLAYBOOK)],
            label='Provisionnement Node.js/npm via Ansible', cwd=fm.ROOT / 'ansible', timeout=1800)

    def test_node_provisioning_fails_closed_if_version_wrong_after_ansible(self):
        with mock.patch.object(fm, 'require_root'), \
             mock.patch.object(fm, 'node_toolchain_issue', side_effect=['node absent', 'node v18 incompatible']), \
             mock.patch.object(fm.shutil, 'which', return_value='/usr/bin/ansible-playbook'), \
             mock.patch.object(fm, 'run_live'), contextlib.redirect_stdout(io.StringIO()):
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


class FrontendFirstReleaseTests(unittest.TestCase):
    """Evite le HTTP 500 avant premier deploy, sans modifier les vhosts tiers."""

    def test_generated_nginx_returns_503_before_first_release(self):
        rendered = fm.nginx_config('auto.ikom.mg', 'backend.auto.ikom.mg', 8172)
        self.assertEqual(rendered.count(fm.NO_RELEASE_MARKER), 1)
        self.assertIn('if (!-f /opt/autokom/frontend/current/index.html)', rendered)
        self.assertIn('return 503', rendered)
        self.assertIn('try_files $uri $uri/ /index.html;', rendered)
        self.assertIn('location ^~ /api/ {', rendered)
        self.assertIn('location = /__autokom/health', rendered)

    def test_migration_preserves_tls_and_is_idempotent(self):
        old = fm.nginx_config('auto.ikom.mg', 'backend.auto.ikom.mg', 8172)
        # Simule la version deja installee avant le garde de premiere release.
        start = old.index('        ' + fm.NO_RELEASE_MARKER)
        end = old.index('        try_files $uri $uri/ /index.html;', start)
        old = old[:start] + old[end:]
        old += '\n# Certbot-managed\nlisten 443 ssl;\nssl_certificate /etc/letsencrypt/live/auto.ikom.mg/fullchain.pem;\n'
        migrated = fm.nginx_release_guard(old)
        self.assertIn('ssl_certificate /etc/letsencrypt/live/auto.ikom.mg/fullchain.pem;', migrated)
        self.assertIn('# Certbot-managed', migrated)
        self.assertEqual(migrated.count(fm.NO_RELEASE_MARKER), 1)
        self.assertEqual(fm.nginx_release_guard(migrated), migrated)

    def test_foreign_nginx_conf_is_never_rewritten(self):
        with self.assertRaises(fm.FrontendError):
            fm.nginx_release_guard('server { listen 443 ssl; location / { return 200; } }')
        with self.assertRaises(fm.FrontendError):
            fm.nginx_release_guard(fm.MARKER + '\nroot /opt/autokom/frontend/current;\nlocation / { return 200; }')

    def test_dns_wait_prints_progress_until_resolved(self):
        output = io.StringIO()
        with mock.patch.object(fm, 'dns_ipv4_visible', side_effect=[False, False, True]) as check, \
             mock.patch.object(fm.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            fm.wait_frontend_dns('auto.ikom.mg', '56.228.86.160', timeout=60, interval=5)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn('tentative 1', output.getvalue())
        self.assertIn('tentative 2', output.getvalue())
        self.assertIn('résolu en IPv4', output.getvalue())

    def test_dns_wait_timeout_reports_duration(self):
        output = io.StringIO()
        with mock.patch.object(fm, 'dns_ipv4_visible', return_value=False), \
             contextlib.redirect_stdout(output), self.assertRaises(fm.FrontendError):
            fm.wait_frontend_dns('auto.ikom.mg', timeout=0, interval=5)
        self.assertIn('tentative 1', output.getvalue())


class FrontendBuilderAccessRegressionTests(unittest.TestCase):
    """Regression for the exact npm EUSAGE caused by inaccessible parent."""

    @unittest.skipUnless(__import__('os').geteuid() == 0, 'needs root for runuser validation')
    def test_builder_can_read_lockfile_after_private_group_traverse(self):
        import os
        import pwd
        import grp
        import shutil
        # Run as an existing unprivileged user only in the isolated temp tree.
        account = 'nobody'
        try:
            user = pwd.getpwnam(account)
        except KeyError:
            self.skipTest('nobody not installed')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            base.chmod(0o711)
            work = base / 'work'
            builds = work / 'builds'
            stage = builds / 'one' / 'source'
            cache = work / 'npm-cache'
            home = work / 'home'
            scanner = work / 'trivy-cache'
            stage.mkdir(parents=True)
            for folder in (cache, home, scanner):
                folder.mkdir()
            for name in ('package.json', 'package-lock.json'):
                shutil.copy2(fm.FRONTEND / name, stage / name)
            for folder in (builds, builds / 'one', stage, cache, home, scanner):
                os.chown(folder, user.pw_uid, user.pw_gid)
                folder.chmod(0o700)
            for name in ('package.json', 'package-lock.json'):
                os.chown(stage / name, user.pw_uid, user.pw_gid)
                (stage / name).chmod(0o600)
            work.chmod(0o700)   # reproduces AWS issue
            with mock.patch.object(fm, 'BUILDER', account), \
                 mock.patch.object(fm, 'NPM_CACHE', cache), \
                 mock.patch.object(fm, 'BUILD_HOME', home), \
                 mock.patch.object(fm, 'SCANNER_CACHE', scanner):
                with self.assertRaisesRegex(fm.FrontendError, 'Accès refusé'):
                    fm.verify_build_access(stage)
                os.chown(work, 0, user.pw_gid)
                work.chmod(0o710)  # root may list; builder group can traverse only
                fm.verify_build_access(stage)

    @unittest.skipUnless(__import__('os').geteuid() == 0, 'needs root for ownership tests')
    def test_builder_prepare_creates_traversable_parent_but_private_children(self):
        import os
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'frontend'
            build = work / 'builds'
            cache = work / 'npm-cache'
            home = work / 'home'
            scanner = work / 'trivy-cache'
            with mock.patch.object(fm, 'BUILDS', build), \
                 mock.patch.object(fm, 'NPM_CACHE', cache), \
                 mock.patch.object(fm, 'WORK_ROOT', work), \
                 mock.patch.object(fm, 'BUILD_HOME', home), \
                 mock.patch.object(fm, 'SCANNER_CACHE', scanner), \
                 mock.patch.object(fm, 'BUILDER', 'root'):
                fm.builder_prepare()
            self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o710)
            self.assertEqual(stat.S_IMODE(build.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(scanner.stat().st_mode), 0o700)

    def test_deploy_verifies_permissions_before_npm(self):
        source = (fm.ROOT / 'scripts/lib/autokom_frontend_manager.py').read_text()
        self.assertLess(source.index('verify_build_access(stage)'), source.index("builder_run(['npm', 'ci'"))

    def test_run_live_protects_logs_and_hides_failure_output(self):
        import os
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            fake_logs = Path(tmp) / 'logs'
            output = io.StringIO()
            with mock.patch.object(fm, 'FRONTEND_LOGS', fake_logs), \
                 mock.patch.object(fm, 'require_root'), \
                 mock.patch.object(fm.subprocess, 'run', side_effect=__import__('subprocess').CalledProcessError(3,['npm'])), \
                 contextlib.redirect_stdout(output):
                with self.assertRaises(fm.FrontendError):
                    fm.run_live(['npm', 'ci'], label='npm test', timeout=1)
            self.assertEqual(len(list(fake_logs.iterdir())), 1)
            self.assertEqual(list(fake_logs.iterdir())[0].stat().st_mode & 0o777, 0o600)
            self.assertNotIn('secret-value', output.getvalue())

    def test_no_release_error_guard_preserves_existing_nginx_https(self):
        self.assertEqual(fm.nginx_config('auto.ikom.mg','backend.auto.ikom.mg',8172).count(fm.NO_RELEASE_MARKER),1)


class FrontendTLSIdempotenceTests(unittest.TestCase):
    def test_reuses_only_certificate_attached_to_expected_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / 'live'
            cert = live / 'auto.ikom.mg'
            cert.mkdir(parents=True)
            (cert / 'fullchain.pem').write_text('CERT')
            (cert / 'privkey.pem').write_text('KEY')
            vhost = root / 'autokom-frontend.conf'
            vhost.write_text(fm.MARKER + '\nserver_name auto.ikom.mg;\n'
                + f'ssl_certificate {cert}/fullchain.pem;\n'
                + f'ssl_certificate_key {cert}/privkey.pem;\n')
            with mock.patch.object(fm, 'VHOST', vhost):
                self.assertTrue(fm.managed_certificate_installed('auto.ikom.mg', live_root=live))
                self.assertFalse(fm.managed_certificate_installed('evil.example.org', live_root=live))
                (cert / 'privkey.pem').unlink()
                self.assertFalse(fm.managed_certificate_installed('auto.ikom.mg', live_root=live))


class FrontendBuilderFullPathTests(unittest.TestCase):
    """Regression: backend's /var/lib/autokom is private (0750)."""

    def test_build_workspace_is_not_nested_in_django_state(self):
        self.assertEqual(fm.WORK_ROOT, Path('/var/lib/autokom-frontend'))
        self.assertEqual(fm.BUILDS, fm.WORK_ROOT / 'builds')
        self.assertEqual(fm.NPM_CACHE, fm.WORK_ROOT / 'npm-cache')
        self.assertEqual(fm.RELEASES, Path('/opt/autokom/frontend/releases'))
        self.assertNotIn('/var/lib/autokom/', str(fm.WORK_ROOT) + '/')

    @unittest.skipUnless(__import__('os').geteuid() == 0, 'needs root to verify real Unix permissions')
    def test_real_builder_access_without_acl_to_backend_parent(self):
        """Use an actual unprivileged process, not mocked ACL/access responses."""
        import os
        import pwd
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o711)
            backend_private = root / 'autokom'
            backend_private.mkdir(mode=0o750)
            backend_private.chmod(0o750)
            backend_private.joinpath('backend-private.txt').write_text('secret')
            work = root / 'autokom-frontend'
            uid = pwd.getpwnam('daemon').pw_uid
            gid = pwd.getpwnam('daemon').pw_gid
            with mock.patch.object(fm, 'WORK_ROOT', work), \
                 mock.patch.object(fm, 'BUILDS', work / 'builds'), \
                 mock.patch.object(fm, 'NPM_CACHE', work / 'npm-cache'), \
                 mock.patch.object(fm, 'BUILD_HOME', work / 'home'), \
                 mock.patch.object(fm, 'SCANNER_CACHE', work / 'trivy-cache'), \
                 mock.patch.object(fm, 'BUILDER', 'daemon'):
                fm.builder_prepare()
                self.assertFalse(fm.builder_access('-x', backend_private))
                self.assertFalse(fm.builder_access('-r', backend_private / 'backend-private.txt'))
                self.assertTrue(fm.builder_access('-x', work))
                self.assertTrue(fm.builder_access('-w', work / 'npm-cache'))
                stage = work / 'builds' / 'test-one' / 'source'
                stage.mkdir(parents=True)
                for directory in (stage.parent, stage):
                    os.chown(directory, uid, gid)
                    directory.chmod(0o700)
                for filename in ('package.json', 'package-lock.json'):
                    file = stage / filename
                    file.write_text('{}')
                    os.chown(file, uid, gid)
                    file.chmod(0o600)
                fm.verify_build_access(stage)
            self.assertEqual(backend_private.stat().st_mode & 0o777, 0o750)


    @unittest.skipUnless(__import__('os').geteuid() == 0, 'needs root for runuser validation')
    def test_builder_home_and_scanner_cache_are_private_writable(self):
        import os
        import pwd
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            base.chmod(0o711)
            work = base / 'frontend'
            stage = work / 'builds' / 'build-one' / 'source'
            cache = work / 'npm-cache'
            home = work / 'home'
            scanner = work / 'trivy-cache'
            stage.mkdir(parents=True)
            for folder in (cache, home, scanner):
                folder.mkdir()
            for folder in (stage, stage.parent, work / 'builds', cache, home, scanner):
                folder.chmod(0o700)
                os.chown(folder, pwd.getpwnam('nobody').pw_uid, pwd.getpwnam('nobody').pw_gid)
            work.chmod(0o711)
            for name in ('package.json','package-lock.json'):
                (stage / name).write_text('{}')
                os.chown(stage / name, pwd.getpwnam('nobody').pw_uid, pwd.getpwnam('nobody').pw_gid)
            with mock.patch.object(fm, 'BUILDER', 'nobody'), \
                 mock.patch.object(fm, 'NPM_CACHE', cache), \
                 mock.patch.object(fm, 'BUILD_HOME', home), \
                 mock.patch.object(fm, 'SCANNER_CACHE', scanner):
                fm.verify_build_access(stage)
                (scanner).chmod(0o500)
                with self.assertRaisesRegex(fm.FrontendError, 'cache des scanners'):
                    fm.verify_build_access(stage)

    def test_build_environment_uses_writable_home_and_scanner_cache(self):
        import sys
        from types import SimpleNamespace
        fake = SimpleNamespace(read_env=lambda _: {})
        with mock.patch.dict(sys.modules, {'autokom_env_manager': fake}):
            env = fm.build_environment(fm.FRONTEND)
        self.assertEqual(env['HOME'], str(fm.BUILD_HOME))
        self.assertEqual(env['TRIVY_CACHE_DIR'], str(fm.SCANNER_CACHE))
        self.assertEqual(env['XDG_CACHE_HOME'], str(fm.BUILD_HOME / '.cache'))
        self.assertEqual(env['NPM_CONFIG_CACHE'], str(fm.NPM_CACHE))
        self.assertNotIn('DJANGO_SECRET_KEY', env)

    @unittest.skipUnless(__import__('os').geteuid() == 0, 'needs root for locked deploy')
    def test_publication_lock_rejects_concurrent_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fm, 'DEPLOY_LOCK', Path(tmp) / 'frontend.lock'):
                with fm.deployment_guard():
                    with self.assertRaisesRegex(fm.FrontendError, 'déploiement .* cours'):
                        with fm.deployment_guard():
                            pass
                with fm.deployment_guard():
                    pass

    def test_work_parent_does_not_change_shared_backend_permissions(self):
        import inspect
        source = inspect.getsource(fm.prepare_builder_parent)
        self.assertNotIn('setfacl', source)
        self.assertNotIn('chmod(', source)
        self.assertNotIn('chown(', source)
        self.assertIn('builder_access', source)


    def test_deploy_and_rollback_use_the_same_lock(self):
        with mock.patch.object(fm, 'deployment_guard') as guard, \
             mock.patch.object(fm, 'deploy_locked', return_value=42) as deploy, \
             mock.patch.object(fm, 'rollback_locked', return_value=33) as rollback:
            self.assertEqual(fm.deploy(object()), 42)
            self.assertEqual(fm.rollback(object()), 33)
            self.assertEqual(guard.call_count, 2)
            deploy.assert_called_once()
            rollback.assert_called_once()
