#!/usr/bin/env python3
"""Régres. v14 : Prometheus init, health wait, grouped forms, secret handling."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, call, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as o

class ServiceReadiness(unittest.TestCase):
    def test_missing_listener_during_startup_is_retried(self):
        with patch.object(o, 'listener', side_effect=[[], [], ['127.0.0.1:9090'], ['127.0.0.1:9090']]) as listener, \
             patch.object(o, 'get_local', side_effect=[(503, ''), (200,'ready')]) as http, \
             patch.object(o.time, 'sleep') as sleep:
            found = o.wait_service_ready('prometheus', 9090, 'http://127.0.0.1:9090/-/ready', attempts=5)
        self.assertEqual(found, ['127.0.0.1:9090'])
        self.assertEqual(listener.call_count, 4)
        self.assertEqual(http.call_count, 2)
        self.assertEqual(sleep.call_count, 3)

    def test_never_claim_ready_before_http_200(self):
        with patch.object(o, 'listener', return_value=['127.0.0.1:9090']), \
             patch.object(o, 'get_local', return_value=(503, '')), \
             patch.object(o.time, 'sleep'), \
             patch.object(o.subprocess, 'run', return_value=SimpleNamespace(stdout='failed')):
            with self.assertRaisesRegex(o.SetupError, r'journalctl -u prometheus'):
                o.wait_service_ready('prometheus', 9090, 'http://127.0.0.1:9090/-/ready', attempts=2, interval=0)

    def test_rejects_public_bind_without_waiting(self):
        with patch.object(o, 'listener', return_value=['*:9090']), \
             patch.object(o, 'get_local') as http, \
             patch.object(o.time, 'sleep') as sleep:
            with self.assertRaisesRegex(o.SetupError, 'hors loopback'):
                o.wait_service_ready('prometheus', 9090, 'http://127.0.0.1:9090/-/ready')
        http.assert_not_called()
        sleep.assert_not_called()

    def test_prometheus_install_waits_after_restart(self):
        args = SimpleNamespace(mode='apply', non_interactive=True, prometheus_domain='')
        events = []
        def fake_run(command, *a, **kw):
            events.append(tuple(command))
            return ''
        def ready(*a, **kw):
            events.append('ready')
            return ['127.0.0.1:9090']
        with patch.object(o, 'package_installed', return_value=False), \
             patch.object(o, 'run', side_effect=fake_run), \
             patch.object(o, 'owned_write', return_value=True), \
             patch.object(o, 'ensure_prometheus_storage') as storage, \
             patch.object(o, 'validate_prom_config'), \
             patch.object(o, 'wait_service_ready', side_effect=ready) as wait, \
             patch.object(o, 'PROM_CONFIG', MagicMock(is_file=MagicMock(return_value=False))), \
             patch.object(o, 'active', return_value=False), \
             patch.object(o, 'autokom_banner'), \
             patch.object(o, 'out'):
            o.ensure_prometheus(args)
        self.assertIn(('apt-get', 'install', '-y', '--no-install-recommends', 'prometheus', 'promtool'), events)
        self.assertLess(events.index(('systemctl', 'restart', 'prometheus')), events.index('ready'))
        storage.assert_called_once()
        wait.assert_called_once()

    def test_tsdb_directory_only_created_if_missing(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp, patch.object(o, 'cfg', return_value=SimpleNamespace(storage_path=tmp + '/metrics2')), \
             patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)), \
             patch.object(o.os, 'chown') as chown, patch.object(o.os, 'chmod'):
            self.assertTrue(o.ensure_prometheus_storage())
            self.assertTrue((Path(tmp)/'metrics2').is_dir())
            self.assertFalse(o.ensure_prometheus_storage())
            chown.assert_called_once()

class CredentialPreflight(unittest.TestCase):
    def test_missing_grafana_password_blocks_apply(self):
        args = SimpleNamespace(mode='apply', prometheus_domain='', prometheus_password='',
                               grafana_new_password='')
        with patch.object(o, 'grafana_requires_initial_admin', return_value=True):
            with self.assertRaisesRegex(o.SetupError, 'Grafana requis'):
                o.preflight_credentials(args)

    def test_prometheus_password_required_if_public_domain(self):
        args = SimpleNamespace(mode='apply', prometheus_domain='prom.example.org',
                               prometheus_password='', grafana_new_password='Password12345678')
        with patch.object(o.PROM_HTPASSWD.__class__, 'exists', return_value=False), \
             patch.object(o, 'grafana_requires_initial_admin', return_value=False):
            with self.assertRaisesRegex(o.SetupError, 'Prometheus requise'):
                o.preflight_credentials(args)

    def test_check_never_requests_credentials(self):
        args = SimpleNamespace(mode='check', prometheus_domain='prom.example.org',
                               prometheus_password='', grafana_new_password='')
        with patch.object(o, 'grafana_requires_initial_admin') as fresh:
            o.preflight_credentials(args)
            fresh.assert_not_called()

class GroupedInteractiveForms(unittest.TestCase):
    def args(self, **kw):
        data = dict(non_interactive=False, mode='apply', prometheus_domain='', grafana_domain='',
                    email='', grafana_user='', public_ip='8.8.8.8',
                    prometheus_user='', prometheus_password='', grafana_new_password='')
        data.update(kw)
        return SimpleNamespace(**data)

    def test_prometheus_entire_form_before_grafana(self):
        args = self.args()
        events=[]
        def choices(question,*a,**k):
            events.append('choice:'+question)
            return True
        def text(label,*a,**k):
            events.append('text:'+label)
            if 'Domaine Prometheus' in label: return 'prom.example.org'
            if 'Let' in label: return 'admin@example.org'
            if 'Utilisateur Prometheus' in label: return 'metricsuser'
            if 'Domaine Grafana' in label: return 'graf.example.org'
            if 'Administrateur Grafana' in label: return 'admin'
            raise AssertionError(label)
        def passwords(label,*a,**k):
            events.append('secret:'+label)
            return 'LongP4ssw0rd!1234'
        with patch.object(o, 'section'),patch.object(o,'yesno',side_effect=choices), \
             patch.object(o, 'prompt_text',side_effect=text), \
             patch.object(o, 'password_twice',side_effect=passwords), \
             patch.object(o, 'prompt_domain',side_effect=lambda label,**kwargs: text(label)), \
             patch.object(o, 'package_installed',return_value=False), \
             patch.object(o, 'existing_vhosts',return_value=[]), \
             patch.object(o, 'PROM_HTPASSWD', MagicMock(exists=MagicMock(return_value=False))), \
             patch.object(o, 'out'):
            o.interactive_options(args)
        self.assertLess(events.index('secret:Prometheus HTTPS'),
                        events.index('choice:Grafana dispose-t-il d’un domaine HTTPS ?'))
        self.assertLess(events.index('text:Domaine Prometheus'), events.index('text:Domaine Grafana'))
        self.assertEqual(args.prometheus_password,'LongP4ssw0rd!1234')
        self.assertEqual(args.grafana_new_password,'LongP4ssw0rd!1234')
        self.assertEqual(args.email,'admin@example.org')
        self.assertEqual(sum('Let' in x for x in events),1)

    def test_check_does_not_request_secrets(self):
        args=self.args(mode='check', email='admin@example.org')
        with patch.object(o,'section'),patch.object(o,'yesno',return_value=False), \
             patch.object(o,'package_installed',return_value=False), \
             patch.object(o, 'password_twice') as pw, patch.object(o, 'secret') as sec, \
             patch.object(o,'out'):
            o.interactive_options(args)
        pw.assert_not_called(); sec.assert_not_called()

    def test_existing_grafana_preserved_no_extra_prompt(self):
        args=self.args(email='admin@example.org')
        events=[]
        def yesno(label, *a, **kw):
            events.append(label)
            return False
        with patch.object(o,'section'),patch.object(o,'yesno',side_effect=yesno), \
             patch.object(o,'package_installed',return_value=True), \
             patch('pathlib.Path.exists',return_value=True), \
             patch.object(o,'active',return_value=False), \
             patch.object(o, 'password_twice') as pw, patch.object(o,'out'):
            o.interactive_options(args)
            with patch.object(o,'grafana_api') as api:
                self.assertIsNone(o.grafana_credentials(False,args))
                api.assert_not_called()
        self.assertEqual(args.grafana_rotate,False)
        self.assertEqual(len(events),3)
        pw.assert_not_called()

    def test_grafana_fresh_uses_collected_password_no_repeat(self):
        args=self.args(grafana_user='grafadmin',grafana_new_password='LongP4ssw0rd!1234')
        with patch.object(o,'grafana_api',side_effect=[(200,'{}'),(200,''),(200,'{}'),(200,'{}'),(200,'{}')]) as api, \
             patch.object(o,'password_twice') as password,patch.object(o,'out'):
            o.grafana_credentials(True,args)
        password.assert_not_called()
        self.assertTrue(any('LongP4ssw0rd!1234' in str(call) for call in api.call_args_list))

if __name__ == '__main__': unittest.main()
