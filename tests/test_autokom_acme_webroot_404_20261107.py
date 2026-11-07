"""Regression: ACME HTTP-01 must serve real token paths before invoking Certbot.

GCP 2026-11-07: Let's Encrypt reached 136.116.89.163:80 but got 404.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as obs


class WebrootRouting(unittest.TestCase):
    def test_gated_and_https_vhosts_both_serve_challenge(self):
        with TemporaryDirectory() as tmp, patch.object(obs, 'ACME_WEBROOT', Path(tmp) / 'acme'):
            for gated in (True, False):
                text = obs.vhost_content('prometheus.ikom.mg', 9090, prometheus=True, gated=gated)
                self.assertIn('location ^~ /.well-known/acme-challenge/', text)
                self.assertIn('try_files $uri =404;', text)
                self.assertIn('root ' + str(Path(tmp) / 'acme') + ';', text)
                if gated:
                    self.assertIn('return 503;', text)
                    self.assertNotIn('proxy_pass', text)
                else:
                    self.assertIn('location / { return 301 https://$host$request_uri; }', text)

    def test_private_root_only_and_symlink_rejected(self):
        if os.geteuid() != 0:
            self.skipTest('AUTOKOM installer requires root')
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            p.chmod(0o755)
            good = p / 'acme'
            with patch.object(obs, 'ACME_WEBROOT', good):
                self.assertEqual(obs.ensure_acme_webroot(), good)
                self.assertEqual((good / '.well-known' / 'acme-challenge').stat().st_mode & 0o777, 0o755)
                self.assertEqual(obs.ensure_acme_webroot(), good)
                (good / '.well-known' / 'acme-challenge').chmod(0o777)
                with self.assertRaisesRegex(obs.SetupError, 'non fiable'):
                    obs.ensure_acme_webroot()
            alias = p / 'alias'
            alias.symlink_to(good)
            with patch.object(obs, 'ACME_WEBROOT', alias):
                with self.assertRaisesRegex(obs.SetupError, 'symbolique'):
                    obs.ensure_acme_webroot()

    def test_local_preflight_sees_correct_token_and_cleans_it(self):
        if os.geteuid() != 0:
            self.skipTest('AUTOKOM installer requires root')
        with TemporaryDirectory() as tmp, patch.object(obs, 'ACME_WEBROOT', Path(tmp) / 'acme'), \
             patch.object(obs, 'out'):
            Path(tmp).chmod(0o755)
            obs.ensure_acme_webroot()
            seen = []
            class Connection:
                def __init__(self, host, port, timeout):
                    self.assert_host = (host, port, timeout)
                def request(self, method, path, headers):
                    seen.append((method, path, headers))
                def getresponse(self):
                    name = seen[-1][1].rsplit('/', 1)[1]
                    class Response:
                        status = 200
                        def read(self, n):
                            return (obs.ACME_WEBROOT / '.well-known' / 'acme-challenge' / name).read_bytes()
                    return Response()
                def close(self): pass
            with patch.object(obs.http.client, 'HTTPConnection', Connection):
                obs.check_acme_http01_local('prometheus.ikom.mg')
            self.assertEqual(seen[0][0], 'GET')
            self.assertEqual(seen[0][2], {'Host': 'prometheus.ikom.mg'})
            self.assertFalse(list((obs.ACME_WEBROOT / '.well-known' / 'acme-challenge').iterdir()))

    def test_local_preflight_fails_before_certbot_if_404(self):
        if os.geteuid() != 0:
            self.skipTest('AUTOKOM installer requires root')
        with TemporaryDirectory() as tmp, patch.object(obs, 'ACME_WEBROOT', Path(tmp) / 'acme'):
            Path(tmp).chmod(0o755)
            obs.ensure_acme_webroot()
            class Connection:
                def __init__(self,*args,**kwargs): pass
                def request(self,*args,**kwargs): pass
                def getresponse(self):
                    class Response:
                        status=404
                        def read(self, n): return b'not found'
                    return Response()
                def close(self): pass
            with patch.object(obs.http.client, 'HTTPConnection', Connection):
                with self.assertRaisesRegex(obs.SetupError, 'HTTP 404'):
                    obs.check_acme_http01_local('prometheus.ikom.mg')
            self.assertFalse(list((obs.ACME_WEBROOT / '.well-known' / 'acme-challenge').iterdir()))

    def test_legacy_autokom_gate_reused_and_acme_via_webroot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o755)
            av, en = root/'av', root/'en'
            av.mkdir(); en.mkdir()
            domain = 'prometheus.ikom.mg'
            managed = av/f'autokom-prometheus-{domain}.conf'
            managed.write_text(obs._old_gated_vhost(domain, 9090), encoding='utf-8')
            (en / managed.name).symlink_to(managed)
            args = SimpleNamespace(mode='apply', non_interactive=True, email='admin@example.com')
            state={'cert':False}
            commands=[]
            def fake_run(command, **kwargs):
                commands.append(command)
                if command[0] == 'certbot':
                    state['cert']=True
                return ''
            def fake_hosts(name):
                if not managed.exists():return []
                text=managed.read_text()
                return [text] if 'TLS PENDING' in text else [text]
            real_write = obs.owned_write
            def safe_write(path,content,**kwargs):
                if str(path).startswith('/etc/letsencrypt/renewal-hooks'):
                    return False
                return real_write(path,content,**kwargs)
            with patch.object(obs,'NGINX_AVAIL',av), patch.object(obs,'NGINX_ENABLED',en), \
                 patch.object(obs,'ACME_WEBROOT',root/'acme'), \
                 patch.object(obs,'existing_vhosts',side_effect=fake_hosts), \
                 patch.object(obs,'valid_cert',side_effect=lambda name:state['cert']), \
                 patch.object(obs,'resolve_domain',return_value=['136.116.89.163']), \
                 patch.object(obs,'run',side_effect=fake_run), \
                 patch.object(obs,'out'), \
                 patch.object(obs,'check_acme_http01_local') as preflight, \
                 patch.object(obs,'owned_write',side_effect=safe_write):
                obs.configure_nginx(domain,9090,'prometheus',args)
            preflight.assert_called_once_with(domain)
            self.assertEqual(len([cmd for cmd in commands if cmd[0]=='certbot']),1)
            cmd=next(cmd for cmd in commands if cmd[0]=='certbot')
            self.assertIn('--webroot',cmd)
            self.assertIn('-w',cmd)
            self.assertEqual(cmd[cmd.index('-w')+1],str(root/'acme'))
            self.assertNotIn('--nginx',cmd)
            self.assertIn('listen 443 ssl;',managed.read_text())
            self.assertIn('location ^~ /.well-known/acme-challenge/',managed.read_text())

if __name__ == '__main__':
    unittest.main()
