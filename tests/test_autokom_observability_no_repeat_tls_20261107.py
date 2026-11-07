"""Regression: repeated prompts, SMTP secret staging, and ACME managed gate."""
import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as obs
import observability_extras as extras


class ManagedTLSRecovery(unittest.TestCase):
    def setup_paths(self, home, domain='prometheus.ikom.mg'):
        avail = home/'avail'; enabled = home/'enabled'
        avail.mkdir(); enabled.mkdir()
        managed = avail/f'autokom-prometheus-{domain}.conf'
        enabled_link = enabled/managed.name
        return avail, enabled, managed, enabled_link

    def test_real_nginx_server_block_has_no_pending_comment(self):
        from nginx_discovery import blocks
        gate = obs.vhost_content('prometheus.ikom.mg',9090,prometheus=True,gated=True)
        server_blocks = list(blocks(gate, 'server'))
        self.assertEqual(len(server_blocks),1)
        self.assertNotIn('TLS PENDING',server_blocks[0])
        self.assertIn('TLS PENDING',gate)

    def test_exact_managed_gate_resumes_when_nginx_omits_header_comment(self):
        with TemporaryDirectory() as tmp:
            av, en, path, link = self.setup_paths(Path(tmp))
            gate = obs.vhost_content('prometheus.ikom.mg',9090,prometheus=True,gated=True)
            path.write_text(gate); link.symlink_to(path)
            status={'valid':False}
            commands=[]
            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                if cmd[0]=='certbot': status['valid']=True
                return ''
            original_write=obs.owned_write
            def safe_write(target, data, **kwargs):
                if str(target).startswith('/etc/letsencrypt/renewal-hooks/'):
                    return True
                return original_write(target,data,**kwargs)
            args=SimpleNamespace(mode='apply',email='ops@example.com',non_interactive=True)
            with patch.object(obs,'NGINX_AVAIL',av), patch.object(obs,'NGINX_ENABLED',en), \
                 patch.object(obs,'existing_vhosts',return_value=[list(__import__('nginx_discovery').blocks(gate,'server'))[0]]), \
                 patch.object(obs,'valid_cert',side_effect=lambda _:status['valid']), \
                 patch.object(obs,'run',side_effect=fake_run), patch.object(obs,'owned_write',side_effect=safe_write), \
                 patch.object(obs,'ensure_acme_webroot'), patch.object(obs,'check_acme_http01_local'), \
                 patch.object(obs,'resolve_domain',return_value=['136.116.89.163']), patch.object(obs,'out'):
                obs.configure_nginx('prometheus.ikom.mg',9090,'prometheus',args)
            self.assertTrue(status['valid'])
            self.assertIn('listen 443 ssl;',path.read_text())
            self.assertEqual(sum(x[0]=='certbot' for x in commands),1)

    def test_known_autokom_https_expiring_cert_reissued_without_replacing_vhost(self):
        with TemporaryDirectory() as tmp:
            av,en,path,link=self.setup_paths(Path(tmp))
            html=obs.vhost_content('prometheus.ikom.mg',9090,prometheus=True,gated=False)
            path.write_text(html); link.symlink_to(path)
            vhosts=list(__import__('nginx_discovery').blocks(html,'server'))
            ready={'valid':False}
            called=[]
            def fake_run(cmd,**kwargs):
                called.append(cmd)
                if cmd[0]=='certbot': ready['valid']=True
                return ''
            args=SimpleNamespace(mode='apply',email='',non_interactive=True)
            with patch.object(obs,'NGINX_AVAIL',av),patch.object(obs,'NGINX_ENABLED',en), \
                 patch.object(obs,'existing_vhosts',return_value=vhosts), \
                 patch.object(obs,'valid_cert',side_effect=lambda _:ready['valid']), \
                 patch.object(obs,'run',side_effect=fake_run), patch.object(obs,'out'), \
                 patch.object(obs,'ensure_acme_webroot'),patch.object(obs,'check_acme_http01_local'):
                obs.configure_nginx('prometheus.ikom.mg',9090,'prometheus',args)
            self.assertEqual(path.read_text(),html)
            self.assertEqual(sum(c[0]=='certbot' for c in called),1)

    def test_unknown_managed_markup_is_not_repaired(self):
        with TemporaryDirectory() as tmp:
            av,en,path,link=self.setup_paths(Path(tmp))
            path.write_text('# AUTOKOM MANAGED prometheus.ikom.mg :9090\nserver { listen 80; server_name prometheus.ikom.mg; location / { return 200; } }')
            link.symlink_to(path)
            args=SimpleNamespace(mode='apply',email='',non_interactive=True)
            with patch.object(obs,'NGINX_AVAIL',av),patch.object(obs,'NGINX_ENABLED',en), \
                 patch.object(obs,'existing_vhosts',return_value=['server { server_name prometheus.ikom.mg; }']), \
                 patch.object(obs,'valid_cert',return_value=False),patch.object(obs,'run') as command:
                with self.assertRaises(obs.SetupError):
                    obs.configure_nginx('prometheus.ikom.mg',9090,'prometheus',args)
                command.assert_not_called()


class PersistentChoices(unittest.TestCase):
    def test_empty_email_is_saved_as_confirmed_choice(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'observability-selection.json'
            args=SimpleNamespace(mode='apply',prometheus_domain='prometheus.ikom.mg',
                grafana_domain='grafana.ikom.mg',email='',grafana_user='AdminGrafana',
                _cert_email_decided=True,_extras_decided=True,setup_node=True,
                setup_cadvisor=True,setup_alertmanager=True,_smtp_enabled=True)
            with patch.object(obs,'CONFIG_PENDING',path),patch.object(obs,'out'):
                obs.save_selection(args)
                saved=obs.previous_selection()
            self.assertEqual(saved['email'],'')
            self.assertTrue(saved['email_decided'])
            self.assertTrue(saved['extras_decided'])
            self.assertTrue(saved['smtp_enabled'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertNotIn('password',path.read_text())

    def test_blank_acme_email_will_not_prompt_again(self):
        args=SimpleNamespace(mode='apply',email='',_cert_email_decided=True)
        with patch.object(obs,'prompt_text') as prompt:
            obs._certificate_email_for(args,'prometheus.ikom.mg')
            prompt.assert_not_called()

    def test_smtp_saved_privately_and_reused_without_any_prompts(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'smtp.json'
            smtp=('smtp-relay.brevo.com',587,'sender@example.com','noreply@example.com','to@example.com','private-password!')
            with patch.object(obs,'SMTP_PENDING',path),patch.object(obs,'out'), \
                 patch.object(extras,'ALERT_CONFIG',Path(tmp)/'absent.yml'):
                extras._save_smtp(obs,smtp)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
                args=SimpleNamespace(mode='apply',non_interactive=False,reconfigure=False,
                    _selection={'extras_decided':True,'setup_node':True,'setup_cadvisor':True,
                                'setup_alertmanager':True,'smtp_enabled':True})
                with patch.object(obs,'yesno') as ask, patch.object(obs,'secret') as pw, patch.object(obs,'prompt_text') as prompt:
                    extras.collect_options(obs,args)
                ask.assert_not_called(); pw.assert_not_called(); prompt.assert_not_called()
                self.assertEqual(args.smtp,smtp)
                self.assertTrue(args.setup_cadvisor)

    def test_symlink_smtp_secret_is_refused(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'smtp'; target=Path(tmp)/'target'
            target.write_text('secret'); path.symlink_to(target)
            with patch.object(obs,'SMTP_PENDING',path):
                with self.assertRaises(obs.SetupError):
                    extras._read_saved_smtp(obs)
            self.assertEqual(target.read_text(),'secret')

    def test_smtp_secret_fails_on_world_readable_permissions(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'smtp'
            path.write_text('{}'); path.chmod(0o644)
            with patch.object(obs,'SMTP_PENDING',path):
                with self.assertRaisesRegex(obs.SetupError,'non privé'):
                    extras._read_saved_smtp(obs)


if __name__=='__main__': unittest.main()
