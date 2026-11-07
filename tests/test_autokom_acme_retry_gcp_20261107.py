"""Tests for an ACME failure after Prometheus/Grafana are already installed."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as obs
import observability_acme as acme


class ACMELogTests(unittest.TestCase):
    def test_detail_from_current_attempt_only(self):
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / 'letsencrypt.log'
            log.write_text('"detail": "old.example.net: Some previous error"\n')
            offset = acme.acme_log_offset(log)
            with log.open('a') as handle:
                handle.write('"detail": "136.116.89.163: Fetching http://prometheus.ikom.mg/.well-known/acme-challenge/public-token: Timeout during connect"\n')
            found = acme.acme_failure_detail('prometheus.ikom.mg', since=offset, path=log)
            self.assertIn('136.116.89.163', found)
            self.assertIn('VPC GCP', found)
            self.assertNotIn('old.example.net', found)
            self.assertNotIn('public-token', found)

    def test_no_stale_diagnosis(self):
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / 'letsencrypt.log'
            log.write_text('"detail": "prometheus.ikom.mg: expired token"\n')
            since = acme.acme_log_offset(log)
            self.assertNotIn('expired token', acme.acme_failure_detail('prometheus.ikom.mg', since=since, path=log))

    def test_refuse_symbolic_log(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'real'; target.write_text('secret')
            link = Path(tmp) / 'log'; link.symlink_to(target)
            self.assertNotIn('secret', acme.acme_failure_detail('prometheus.ikom.mg', path=link))


class ACMEReentry(unittest.TestCase):
    def test_retry_existing_managed_gate_then_publish_https(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            avail, enabled = root/'available', root/'enabled'
            avail.mkdir(); enabled.mkdir()
            domain = 'prometheus.example.org'
            managed = avail/f'autokom-prometheus-{domain}.conf'
            gate = obs.vhost_content(domain, 9090, prometheus=True, gated=True)
            state = {'cert': False, 'fail_once': True}
            args = SimpleNamespace(mode='apply', email='admin@example.org', non_interactive=True)
            commands = []
            real_write = obs.owned_write
            def safe_write(path, content, **kwargs):
                if str(path).startswith('/etc/letsencrypt/renewal-hooks/'):
                    return False
                return real_write(path, content, **kwargs)
            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                if cmd[0] == 'certbot':
                    if state['fail_once']:
                        state['fail_once'] = False
                        raise obs.SetupError('certbot a échoué (code 1)')
                    state['cert'] = True
                return ''
            def vhosts(name):
                return [gate] if managed.exists() and 'TLS PENDING' in managed.read_text() else []
            with patch.object(obs, 'NGINX_AVAIL', avail), patch.object(obs, 'NGINX_ENABLED', enabled), \
                 patch.object(obs, 'existing_vhosts', side_effect=vhosts), \
                 patch.object(obs, 'valid_cert', side_effect=lambda d: state['cert']), \
                 patch.object(obs, 'resolve_domain', return_value=['136.116.89.163']), \
                 patch.object(obs, 'run', side_effect=fake_run), patch.object(obs, 'owned_write', side_effect=safe_write), \
                 patch.object(obs, 'out'), patch.object(obs, 'ensure_acme_webroot'), \
                 patch.object(obs, 'check_acme_http01_local'), \
                 patch.object(acme, 'acme_failure_detail', return_value='TCP/80 inaccessible'), \
                 patch.object(acme, 'acme_log_offset', return_value=0):
                with self.assertRaisesRegex(obs.SetupError, 'TCP/80 inaccessible'):
                    obs.configure_nginx(domain, 9090, 'prometheus', args)
                self.assertTrue(managed.is_file())
                self.assertIn('TLS PENDING', managed.read_text())
                self.assertTrue((enabled/managed.name).is_symlink())
                self.assertFalse(state['cert'])
                obs.configure_nginx(domain, 9090, 'prometheus', args)
                self.assertTrue(state['cert'])
                self.assertIn('listen 443 ssl;', managed.read_text())
                self.assertEqual(sum(c[0]=='certbot' for c in commands), 2)

    def test_foreign_vhost_is_not_replaced(self):
        args = SimpleNamespace(mode='apply', email='admin@example.org', non_interactive=True)
        with patch.object(obs, 'existing_vhosts', return_value=['server { server_name prometheus.example.org; proxy_pass http://another; }']):
            with self.assertRaisesRegex(obs.SetupError, 'non géré par AUTOKOM'):
                obs.configure_nginx('prometheus.example.org', 9090, 'prometheus', args)

    def test_different_managed_gate_is_not_adopted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'av').mkdir(); (root/'en').mkdir()
            path = root/'av'/'autokom-prometheus-prometheus.example.org.conf'
            path.write_text('# AUTOKOM MANAGED wrong\nserver {}\n')
            (root/'en'/path.name).symlink_to(path)
            args = SimpleNamespace(mode='apply', email='admin@example.org', non_interactive=True)
            with patch.object(obs, 'NGINX_AVAIL', root/'av'), patch.object(obs, 'NGINX_ENABLED', root/'en'), \
                 patch.object(obs, 'existing_vhosts', return_value=['TLS PENDING unrelated']), \
                 patch.object(obs, 'valid_cert', return_value=False):
                with self.assertRaises(obs.SetupError):
                    obs.configure_nginx('prometheus.example.org', 9090, 'prometheus', args)


if __name__ == '__main__':
    unittest.main()
