"""Regression for repeated HTTP-01 404 on GCP using existing Cloudflare DNS.

No real Cloudflare API or Let's Encrypt calls; test token handling, safe setup,
method selection, Certbot CLI, and HTTP-only backward compatibility.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as obs
import observability_dns01 as dns


class CloudflareDNS01Config(unittest.TestCase):
    def test_no_config_keeps_existing_http01(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(dns.configured_zone_credentials('grafana.ikom.mg',env_path=Path(temp)/'none'))

    def test_config_with_scoped_zone_is_reused(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'cloudflare-dns.env'
            path.write_text('CLOUDFLARE_ZONE_NAME=ikom.mg\nCLOUDFLARE_ZONE_ID='+'a'*32+'\nCLOUDFLARE_API_TOKEN=FAKE_TOKEN-123\n')
            path.chmod(0o600)
            self.assertEqual(dns.configured_zone_credentials('prometheus.ikom.mg',env_path=path),
                             ('ikom.mg','FAKE_TOKEN-123'))
            self.assertIsNone(dns.configured_zone_credentials('other.example.com',env_path=path))

    def test_rejects_world_readable_tokens(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'cloudflare-dns.env'
            path.write_text('CLOUDFLARE_ZONE_NAME=ikom.mg\nCLOUDFLARE_API_TOKEN=notreal\n')
            path.chmod(0o644)
            with self.assertRaisesRegex(dns.DNS01Error,'0600'):
                dns.configured_zone_credentials('prometheus.ikom.mg',env_path=path)

    def test_rejects_symlink_and_invalid_zone_id(self):
        with TemporaryDirectory() as temp:
            a=Path(temp)/'orig'
            a.write_text('CLOUDFLARE_ZONE_NAME=ikom.mg\nCLOUDFLARE_ZONE_ID=invalid\nCLOUDFLARE_API_TOKEN=FAKE\n')
            a.chmod(0o600)
            with self.assertRaisesRegex(dns.DNS01Error, 'ZONE_ID'):
                dns.configured_zone_credentials('grafana.ikom.mg',env_path=a)
            b=Path(temp)/'link'
            b.symlink_to(a)
            with self.assertRaisesRegex(dns.DNS01Error,'symbolique'):
                dns.configured_zone_credentials('grafana.ikom.mg',env_path=b)

    def test_rejects_bad_token_without_echo(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'cloudflare-dns.env'
            path.write_text('CLOUDFLARE_ZONE_NAME=ikom.mg\nCLOUDFLARE_ZONE_ID='+'a'*32+'\nCLOUDFLARE_API_TOKEN=BAD SECRET\n')
            path.chmod(0o600)
            with self.assertRaises(dns.DNS01Error) as err:
                dns.configured_zone_credentials('grafana.ikom.mg',env_path=path)
            self.assertNotIn('BAD SECRET',str(err.exception))


@unittest.skipUnless(os.geteuid() == 0, 'AUTOKOM root-only file management')
class CloudflareDNS01Storage(unittest.TestCase):
    def test_creates_root_private_file_and_reuses_without_rewriting(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)/'autokom'
            path=dns.ensure_certbot_credentials('FAKE-SECRET',folder=folder)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(folder.stat().st_mode & 0o777,0o700)
            self.assertEqual(path.stat().st_uid,0)
            self.assertEqual(dns.ensure_certbot_credentials('FAKE-SECRET',folder=folder),path)
            self.assertIn('dns_cloudflare_api_token = FAKE-SECRET',path.read_text())

    def test_safe_token_rotation_only_owned_file(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)/'autokom'
            path=dns.ensure_certbot_credentials('OLD-FAKE',folder=folder)
            dns.ensure_certbot_credentials('NEW-FAKE',folder=folder)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertIn('NEW-FAKE',path.read_text())
            self.assertNotIn('OLD-FAKE',path.read_text())

    def test_never_overwrites_foreign_credentials(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)/'autokom'; folder.mkdir(mode=0o700)
            path=folder/'cloudflare-dns.ini'
            path.write_text('foreign config\n'); path.chmod(0o600)
            with self.assertRaisesRegex(dns.DNS01Error,'non géré'):
                dns.ensure_certbot_credentials('FAKE-SECRET',folder=folder)
            self.assertEqual(path.read_text(),'foreign config\n')

    def test_refuses_untrusted_directory_and_symlink_credentials(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)/'autokom'; folder.mkdir(mode=0o755)
            with self.assertRaisesRegex(dns.DNS01Error,'0700'):
                dns.ensure_certbot_credentials('FAKE-SECRET',folder=folder)
            folder.chmod(0o700)
            target=Path(temp)/'foreign'; target.write_text('Do not touch')
            (folder/'cloudflare-dns.ini').symlink_to(target)
            with self.assertRaisesRegex(dns.DNS01Error,'symbolique'):
                dns.ensure_certbot_credentials('FAKE-SECRET',folder=folder)
            self.assertEqual(target.read_text(),'Do not touch')


class CertbotPluginTests(unittest.TestCase):
    def test_detects_plugin_in_certbot_output(self):
        result=SimpleNamespace(returncode=0,stdout='* dns-cloudflare\nDescription: DNS Authenticator')
        with patch.object(dns.subprocess,'run',return_value=result):
            self.assertTrue(dns.plugin_available())

    def test_installs_only_when_plugin_missing(self):
        run_calls=[]
        dns.ensure_dns_plugin(lambda argv,**kw: run_calls.append(argv), check=lambda: True)
        self.assertEqual(run_calls,[])
        checks=iter([False,True])
        dns.ensure_dns_plugin(lambda argv,**kw: run_calls.append(argv),check=lambda: next(checks))
        self.assertEqual(run_calls[0],['apt-get','install','-y','--no-install-recommends',dns.PLUGIN_PACKAGE])

    def test_apt_metadata_is_refreshed_once_if_package_install_fails(self):
        calls=[]
        checks=iter([False,True])
        def run(argv, **kwargs):
            calls.append(argv)
            if len(calls) == 1:
                raise RuntimeError('package lists stale')
        dns.ensure_dns_plugin(run, check=lambda: next(checks))
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[1],['apt-get','update'])
        self.assertEqual(calls[0],calls[2])

    def test_absent_after_install_fails_instead_of_claiming_success(self):
        with self.assertRaisesRegex(dns.DNS01Error,'ne détecte pas'):
            dns.ensure_dns_plugin(lambda *a,**k: None,check=lambda: False)

    def test_certbot_arguments_have_no_token(self):
        args=dns.certbot_dns01_args('grafana.ikom.mg',Path('/root/secret/cloudflare.ini'))
        self.assertEqual(args[0],'--dns-cloudflare')
        self.assertIn('60',args)
        self.assertNotIn('FAKE_TOKEN',str(args))


class ObservabilitySelectionTests(unittest.TestCase):
    def test_dns01_skips_broken_acme_http_preflight_and_reuses_admin(self):
        args=SimpleNamespace(email='',_cert_email_warning_emitted=False)
        with patch.object(obs,'valid_cert',side_effect=[False,True]), \
             patch('observability_dns01.configured_zone_credentials',return_value=('ikom.mg','FAKE_TOKEN')), \
             patch('observability_dns01.ensure_dns_plugin') as plugin, \
             patch('observability_dns01.ensure_certbot_credentials',return_value=Path('/etc/letsencrypt/autokom/cloudflare-dns.ini')), \
             patch.object(obs,'check_acme_http01_local',side_effect=AssertionError('HTTP should not be probed')) as http, \
             patch.object(obs,'run') as run,patch.object(obs,'out'):
            obs.issue_observability_certificate('prometheus.ikom.mg',args)
            plugin.assert_called_once()
            http.assert_not_called()
            command=run.call_args.args[0]
            self.assertEqual(command[:3],['certbot','certonly','--non-interactive'])
            self.assertIn('--cert-name',command)
            self.assertEqual(command[command.index('--cert-name')+1],'prometheus.ikom.mg')
            self.assertIn('--dns-cloudflare',command)
            self.assertNotIn('--webroot',command)
            self.assertNotIn('FAKE_TOKEN',' '.join(command))

    def test_no_cloudflare_still_uses_webroot_preflight(self):
        args=SimpleNamespace(email='operator@example.com')
        with patch.object(obs,'valid_cert',side_effect=[False,True]), \
             patch('observability_dns01.configured_zone_credentials',return_value=None), \
             patch.object(obs,'ensure_acme_webroot'), \
             patch.object(obs,'check_acme_http01_local') as http, \
             patch.object(obs,'run') as run,patch.object(obs,'out'):
            obs.issue_observability_certificate('prometheus.example.com',args)
            http.assert_called_once_with('prometheus.example.com')
            self.assertIn('--webroot',run.call_args.args[0])
            self.assertNotIn('--dns-cloudflare',run.call_args.args[0])

    def test_reuse_valid_certificate_has_no_side_effect(self):
        args=SimpleNamespace(email='')
        with patch.object(obs,'valid_cert',return_value=True), \
             patch('observability_dns01.configured_zone_credentials') as config, \
             patch.object(obs,'run') as run,patch.object(obs,'out'):
            obs.issue_observability_certificate('prometheus.ikom.mg',args)
            run.assert_not_called(); config.assert_not_called()

    def test_invalid_cloudflare_config_never_falls_back_to_failing_http(self):
        args=SimpleNamespace(email='')
        with patch.object(obs,'valid_cert',return_value=False), \
             patch('observability_dns01.configured_zone_credentials',side_effect=dns.DNS01Error('Cloudflare invalid')), \
             patch.object(obs,'check_acme_http01_local') as http,patch.object(obs,'run') as run,patch.object(obs,'out'):
            with self.assertRaisesRegex(obs.SetupError,'Cloudflare invalid'):
                obs.issue_observability_certificate('grafana.ikom.mg',args)
            http.assert_not_called(); run.assert_not_called()

if __name__=='__main__': unittest.main()
