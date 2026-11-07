"""2026-11-07 regression: Node on VPN:443 must coexist with nginx on LAN+loopback."""
from __future__ import annotations
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
spec = importlib.util.spec_from_file_location('autokom_443_repair', ROOT / 'scripts' / 'repair' / 'repair_nginx_443_bind.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
import autokom_nginx_bind_policy as policy
import autokom_nginx_runtime as runtime
import autokom_ingress_health as ingress
import observability as obs
import nginx_discovery

class AddressPartitionRegression(unittest.TestCase):
    def test_anywhere_ufw_on_ens4_does_not_touch_loopback(self):
        from firewall import rule_command
        self.assertEqual(rule_command('deny', 'ens4', 9090),
                         ['ufw', 'insert', '1', 'deny', 'in', 'on', 'ens4', 'to', 'any', 'port', '9090', 'proto', 'tcp'])

    def test_foreign_vpn_node_allowed_but_wildcard_is_not(self):
        node = runtime.Listener(443, 'node', 637, '10.77.77.1:443')
        self.assertIsNone(repair.forbidden_listener({443: [node]}, '10.128.0.4'))
        for ip in ('127.0.0.1:443', '10.128.0.4:443', '0.0.0.0:443', '[::]:443'):
            self.assertIsNotNone(repair.forbidden_listener({443: [runtime.Listener(443, 'node', 637, ip)]}, '10.128.0.4'))

    def test_transform_preserves_tls_acl_redirect_http_and_ipv6(self):
        original = ('server {\n    listen 80;\n    server_name prometheus.ikom.mg;\n'
                    '    location / { return 301 https://$host$request_uri; }\n}\n'
                    'server {\n    listen 443 ssl; # managed\n'
                    '    auth_basic "metrics";\n    server_name prometheus.ikom.mg;\n}\n'
                    'server {\n    listen 443 ssl default_server;\n'
                    '    listen [::]:443 ssl default_server;\n    return 404;\n}\n')
        changed, count = repair.rewritten(original, '10.128.0.4')
        self.assertEqual(count, 2)
        self.assertIn('listen 10.128.0.4:443 ssl; # managed', changed)
        self.assertIn('listen 127.0.0.1:443 ssl;', changed)
        self.assertIn('listen 10.128.0.4:443 ssl default_server;', changed)
        self.assertIn('listen [::]:443 ssl default_server;', changed)
        self.assertIn('auth_basic "metrics";', changed)
        self.assertIn('listen 80;', changed)
        self.assertIn('return 301', changed)

    def test_refuse_IPV6_dual_stack(self):
        with self.assertRaisesRegex(repair.RepairError, 'IPv6'):
            repair.rewritten('listen [::]:443 ssl ipv6only=off;', '10.128.0.4')

    def test_untrusted_explicit_ip_443_refused(self):
        with self.assertRaisesRegex(repair.RepairError, 'non standard'):
            repair.rewritten('listen 10.77.77.1:443 ssl;', '10.128.0.4')

    def test_policy_default_does_not_regress_any_legacy_vhost(self):
        with TemporaryDirectory() as tmp, mock.patch.object(policy, 'STATE', Path(tmp)/'absent'):
            self.assertEqual(policy.https_listens(), '    listen 443 ssl;\n')
            self.assertEqual(ingress.expected_catchall(), ingress.CATCHALL)
            self.assertIn('listen 443 ssl;', obs.vhost_content('prom.ikom.mg',9090,prometheus=True,gated=False))

    def test_policy_applies_to_future_generated_vhost_and_catchall(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'policy.json'
            path.write_text(json.dumps({'schema':1,'port':443,'addresses':['10.128.0.4','127.0.0.1']}))
            path.chmod(0o600)
            with mock.patch.object(policy, 'STATE', path):
                self.assertEqual(policy.addresses(), ('10.128.0.4', '127.0.0.1'))
                vhost=obs.vhost_content('prometheus.ikom.mg',9090,prometheus=True,gated=False)
                self.assertIn('listen 10.128.0.4:443 ssl;',vhost)
                self.assertIn('listen 127.0.0.1:443 ssl;',vhost)
                self.assertNotIn('listen 443 ssl;',vhost)
                self.assertTrue(nginx_discovery.nginx_sites(vhost)['prometheus.ikom.mg'].tls_configured)
                self.assertIn('listen 10.128.0.4:443 ssl default_server;', ingress.expected_catchall())
                self.assertIn('listen 127.0.0.1:443 ssl default_server;', ingress.expected_catchall())

    def test_stale_policy_rejects_symlink_and_open_modes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'policy.json'
            path.write_text(json.dumps({'schema':1,'port':443,'addresses':['10.128.0.4','127.0.0.1']}))
            path.chmod(0o644)
            with self.assertRaises(policy.BindPolicyError):
                policy.addresses(state_path=path)
            path.chmod(0o600)
            alias=Path(tmp)/'alias'
            alias.symlink_to(path)
            with self.assertRaises(policy.BindPolicyError):
                policy.addresses(state_path=alias)

    def test_affected_files_only_and_unmanaged_detection(self):
        with TemporaryDirectory() as tmp:
            base=Path(tmp)
            folder=base/'sites-available';folder.mkdir()
            v=folder/'site.conf';v.write_text('server {\nlisten 443 ssl;\n}\n')
            with mock.patch.object(repair, 'SAFE_PATHS', (folder,)):
                dump=f'# configuration file {v}:\nserver {{\nlisten 443 ssl;\n}}\n'
                changes=repair.plan(dump,'10.128.0.4')
            self.assertEqual(list(changes),[v])
            self.assertIn('listen 10.128.0.4:443 ssl;',changes[v][1])
            self.assertEqual(v.read_text(), 'server {\nlisten 443 ssl;\n}\n')

    def test_refuses_to_touch_nginx_conf_root(self):
        with TemporaryDirectory() as tmp:
            target=Path(tmp)/'nginx.conf'
            target.write_text('server {\nlisten 443 ssl;\n}')
            with self.assertRaisesRegex(repair.RepairError, 'intervention manuelle'):
                repair.plan(f'# configuration file {target}:\n{target.read_text()}','10.128.0.4')

if __name__ == '__main__': unittest.main()

class TransactionRegression(unittest.TestCase):
    def fixture(self, tmp):
        path = Path(tmp) / 'site.conf'
        path.write_text('server {\n    listen 443 ssl;\n    server_name prom.test;\n}\n')
        original = path.read_text()
        updated, n = repair.rewritten(original, '10.128.0.4')
        self.assertEqual(n, 1)
        return path, original, {path: (original, updated, path.stat())}

    def sock(self):
        return {443: [runtime.Listener(443, 'node', 637, '10.77.77.1:443'),
                      runtime.Listener(443, 'nginx', 999, '10.128.0.4:443'),
                      runtime.Listener(443, 'nginx', 999, '127.0.0.1:443')]}

    def test_apply_success_persists_and_preserves_vpn_listener(self):
        with TemporaryDirectory() as tmp:
            path, original, changes = self.fixture(tmp)
            state = Path(tmp) / 'nginx-state.json'
            with mock.patch.object(repair, 'BACKUPS', Path(tmp) / 'backup'), \
                 mock.patch.object(repair, 'POLICY_STATE', state), \
                 mock.patch.object(policy, 'STATE', state), \
                 mock.patch.object(repair, 'listeners', return_value=self.sock()), \
                 mock.patch.object(repair, 'run', return_value=''), \
                 mock.patch.object(repair, 'verify_local_domains') as verify, \
                 mock.patch.object(repair.time, 'sleep'):
                repair.apply(changes, '10.128.0.4')
            self.assertIn('listen 10.128.0.4:443 ssl;', path.read_text())
            self.assertEqual(json.loads(state.read_text())['addresses'], ['10.128.0.4', '127.0.0.1'])
            verify.assert_called_once_with()
            self.assertTrue(list((Path(tmp) / 'backup').glob('nginx-443-*/*.conf')))

    def test_nginx_test_failure_restores_exact_previous_bytes(self):
        with TemporaryDirectory() as tmp:
            path, original, changes = self.fixture(tmp)
            state = Path(tmp) / 'nginx-state.json'
            calls = []
            def reject_first_test(args):
                calls.append(args)
                if args == ['nginx', '-t'] and calls.count(['nginx', '-t']) == 1:
                    raise repair.RepairError('configuration refusée')
                return ''
            with mock.patch.object(repair, 'BACKUPS', Path(tmp) / 'backup'), \
                 mock.patch.object(repair, 'POLICY_STATE', state), \
                 mock.patch.object(repair, 'listeners', return_value=self.sock()), \
                 mock.patch.object(repair, 'run', side_effect=reject_first_test), \
                 mock.patch.object(repair.time, 'sleep'):
                with self.assertRaisesRegex(repair.RepairError, 'annulée'):
                    repair.apply(changes, '10.128.0.4')
            self.assertEqual(path.read_text(), original)
            self.assertFalse(state.exists())

    @unittest.skipUnless(shutil.which('nginx'), 'Nginx absent')
    def test_real_nginx_accepts_two_ipv4_listen_default_servers(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            content = ('worker_processes 1;\n'
                       f'error_log {base}/error.log;\n'
                       f'pid {base}/nginx.pid;\n'
                       'events { worker_connections 64; }\n'
                       'http {\n'
                       ' server {\n'
                       '  listen 127.0.0.1:18443 ssl default_server;\n'
                       '  listen 127.0.0.2:18443 ssl default_server;\n'
                       '  server_name _;\n'
                       '  ssl_reject_handshake on;\n'
                       ' }\n'
                       ' server {\n'
                       '  listen 127.0.0.1:18443 ssl;\n'
                       '  listen 127.0.0.2:18443 ssl;\n'
                       '  server_name prom.test;\n'
                       '  ssl_reject_handshake on;\n'
                       ' }\n'
                       '}\n')
            config = base / 'nginx.conf'
            config.write_text(content)
            proc = subprocess.run(['nginx', '-t', '-p', str(base) + '/', '-c', str(config)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class CLIIntegration(unittest.TestCase):
    def test_nginx_repair_direct_cli_check(self):
        import autokom_cli as cli
        with mock.patch('autokom_security.configured', return_value=False), \
             mock.patch.object(cli,'call_module') as called:
            result=cli.main(['tools','nginx','repair','--check'])
        self.assertEqual(result,0)
        called.assert_called_once_with('scripts/repair/repair_nginx_443_bind.py','--check')

    def test_nginx_repair_no_apply_without_explicit_flag(self):
        import autokom_cli as cli
        with mock.patch('autokom_security.configured', return_value=False), \
             mock.patch.object(cli,'call_module') as called:
            with self.assertRaises(SystemExit):
                cli.main(['tools','nginx','repair'])
        called.assert_not_called()
