#!/usr/bin/env python3
"""Regression coverage for AUTOKOM interactive UX v3; no real service modifications."""
import argparse
import os
from pathlib import Path
import pty
import select
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import nginx_discovery as nd
import services
import menu

NGINX = '''# configuration file /etc/nginx/nginx.conf:
upstream jenkins_ik0m_tech_backend {
 server 127.0.0.1:8080;
 keepalive 64;
}
server {
 server_name jenkins.ik0m.tech;
 location / {
  proxy_pass http://jenkins_ik0m_tech_backend;
 }
 listen 443 ssl;
 ssl_certificate /etc/letsencrypt/live/jenkins.ik0m.tech/fullchain.pem;
 ssl_certificate_key /etc/letsencrypt/live/jenkins.ik0m.tech/privkey.pem;
}
server {
 server_name jenkins.ik0m.tech;
 listen 80;
 return 301 https://$host$request_uri;
}
server {
 server_name another.example.com;
 listen 443 ssl;
 ssl_certificate /some/other.pem;
 location / { proxy_pass http://127.0.0.1:3000; }
}
'''


def opts(**kwargs):
    defaults = dict(mode='check', db=None, bind=None, allow='', create_app_db=False,
                    docker_access=None, domain=None, tls=None, email=None, cert=None, key=None,
                    unsafe_all=False, allow_public_clients=False, confirm_restart=False,
                    non_interactive=False)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class DomainTests(unittest.TestCase):
    def test_existing_jenkins_proxy_cert_detected(self):
        sites = nd.nginx_sites(NGINX, lambda path, domain: domain == 'jenkins.ik0m.tech')
        self.assertEqual(nd.jenkins_domains(sites), ['jenkins.ik0m.tech'])
        self.assertEqual(nd.domain_plan('jenkins.ik0m.tech', sites), 'existing')
        self.assertEqual(nd.domain_plan('new.ik0m.tech', sites), 'new')
        self.assertFalse(sites['another.example.com'].proxy_ok)

    def test_refuse_conflicting_domain(self):
        sites = nd.nginx_sites(NGINX, lambda p, d: True)
        with self.assertRaisesRegex(ValueError, 'déjà utilisé'):
            nd.domain_plan('another.example.com', sites)

    def test_refuse_missing_or_expiring_certificate(self):
        sites = nd.nginx_sites(NGINX, lambda p, d: False)
        with self.assertRaisesRegex(ValueError, 'TLS'):
            nd.domain_plan('jenkins.ik0m.tech', sites)

    def test_existing_never_edits_proxy(self):
        op = opts(domain='jenkins.ik0m.tech', tls='existing')
        sites = nd.nginx_sites(NGINX, lambda p,d: True)
        with mock.patch.object(services, 'nginx_inventory', return_value=sites), \
             mock.patch.object(services, 'atomic_write', side_effect=AssertionError('write')), \
             mock.patch.object(services, 'run', side_effect=AssertionError('process')), \
             mock.patch.object(services, 'say'):
            services.configure_proxy(op)


class InputTests(unittest.TestCase):
    def test_comma_semicolon_space(self):
        self.assertEqual(services.cidrs('172.18.0.0/16,172.19.0.0/16; 10.0.0.0/24'),
                         ['172.18.0.0/16', '172.19.0.0/16', '10.0.0.0/24'])
        with self.assertRaisesRegex(ValueError, 'Séparer les réseaux'):
            services.cidrs('172.18.0.0/16-172.19.0.0/16')
        with self.assertRaises(ValueError):
            services.cidrs('0.0.0.0/0')

    def test_no_duplicate_bind_confirmation_or_irrelevant_cidr(self):
        op = opts(db='mysql')
        prompts = []
        choices = iter([2, 1, 1, 2]) # bind all, no new db, Docker no, no domain
        def pick_fake(label, values, **kwargs):
            prompts.append(label)
            return next(choices)
        with mock.patch.object(services, 'pick', side_effect=pick_fake), \
             mock.patch.object(services, 'ask', side_effect=AssertionError('unexpected question')), \
             mock.patch.object(services, 'nginx_inventory', return_value={}), \
             mock.patch.object(services, 'say'):
            op = services.interactive(op)
        self.assertEqual(op.bind, 'all')
        self.assertFalse(op.create_app_db)
        self.assertEqual(op.allow, '')
        self.assertEqual(op.domain, '')
        self.assertEqual(len(prompts), 4)
        self.assertEqual(services.check_user_input(op).bind, 'all')

    def test_existing_domain_skips_certbot_and_email(self):
        op = opts(db='mysql', bind='all', create_app_db=False, docker_access=False)
        sites = nd.nginx_sites(NGINX, lambda p,d: True)
        def pick_fake(label, values, **kwargs):
            return 1
        with mock.patch.object(services, 'pick', side_effect=pick_fake), \
             mock.patch.object(services, 'ask', side_effect=AssertionError('unnecessary DNS/TLS question')), \
             mock.patch.object(services, 'nginx_inventory', return_value=sites), \
             mock.patch.object(services, 'say'):
            result = services.interactive(op)
        self.assertEqual(result.domain, 'jenkins.ik0m.tech')
        self.assertEqual(result.tls, 'existing')
        self.assertIsNone(result.email)

    def test_new_domain_requires_email_if_certbot(self):
        op = opts(db='mysql', bind='local', create_app_db=False, docker_access=False)
        replies = iter(['jenkins.new.example.com','admin@example.com'])
        picks = iter([1, 1, 1])  # no new DB; new domain; Certbot
        with mock.patch.object(services, 'pick', side_effect=lambda *a, **kw: next(picks)), \
             mock.patch.object(services, 'ask', side_effect=lambda *a, **kw: next(replies)), \
             mock.patch.object(services, 'nginx_inventory', return_value={}), \
             mock.patch.object(services, 'say'):
            result = services.interactive(op)
        self.assertEqual(result.domain,'jenkins.new.example.com')
        self.assertEqual(result.tls, 'certbot')
        self.assertEqual(result.email, 'admin@example.com')

    def test_tty_menu_hint_removed(self):
        child, master = pty.fork()
        if child == 0:
            try:
                r = menu.pick('BIND?', ['Local', 'Toutes interfaces'], hint='Explication jaune temporaire')
                print('SELECTED=', r, flush=True)
                os._exit(0)
            except BaseException as exc:
                print('ERROR', repr(exc), flush=True)
                os._exit(2)
        try:
            time.sleep(.15)
            os.write(master, b'2')
            out = bytearray()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                readable,_,_=select.select([master],[],[],0.1)
                if readable:
                    try: out.extend(os.read(master,4096))
                    except OSError: break
                if b'SELECTED= 2' in out: break
            _, status=os.waitpid(child,0)
            self.assertEqual(os.WEXITSTATUS(status),0,out.decode(errors='ignore'))
            self.assertIn(b'SELECTED= 2',out)
            self.assertIn(b'\x1b[33m',out)
            self.assertIn(b'\x1b[2K',out)
        finally:
            os.close(master)


if __name__ == '__main__':
    unittest.main(verbosity=2)
