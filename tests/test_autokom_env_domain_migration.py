"""Offline checks: no real DNS, systemd, Jenkins, Docker or MySQL changes."""
import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_env_manager as env
import autokom_domain_migration as domain

class TestEnvironment(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.enterContext(patch.object(env,'SECRET_DIR',self.folder))
        self.enterContext(patch.object(env.os,'geteuid',return_value=0))

    def test_same_place_and_names(self):
        self.assertEqual(env.file_path('runtime'),self.folder/'runtime.env')
        self.assertEqual(env.file_path('backend'),self.folder/'backend.env')
        self.assertEqual(env.file_path('cloudflare'),self.folder/'cloudflare-dns.env')
        self.assertEqual(env.file_path('frontend'),self.folder/'frontend.env')

    def test_never_symlink(self):
        (self.folder/'actual').write_text('DB_NAME=ok\n')
        (self.folder/'backend.env').symlink_to(self.folder/'actual')
        with self.assertRaises(env.EnvError):env.write_env(self.folder/'backend.env',{'DB_NAME':'ok'})

    def test_mode_atomic_secret_stays_private(self):
        secret='very_strong_test_password=pl@ce\\"here'
        env.write_env(env.file_path('runtime'),{'AUTOKOM_JENKINS_API_TOKEN':secret})
        self.assertEqual(env.file_path('runtime').stat().st_mode & 0o777,0o600)
        self.assertEqual(env.read_env(env.file_path('runtime'))['AUTOKOM_JENKINS_API_TOKEN'],secret)
        out=io.StringIO()
        with redirect_stdout(out):env.show('runtime')
        self.assertNotIn(secret,out.getvalue())
        self.assertIn('configuré',out.getvalue())

    def test_allow_old_duplicate_keys(self):
        path=env.file_path('backend')
        path.write_text('DEFAULT_FROM_EMAIL=one@example.com\nDEFAULT_FROM_EMAIL=two@example.com\n')
        self.assertEqual(env.read_env(path)['DEFAULT_FROM_EMAIL'],'two@example.com')

    def test_reject_multiline(self):
        with self.assertRaises(env.EnvError):env.write_env(env.file_path('runtime'),{'KEY':'yes\nBAD=1'})

    def test_backups_are_private(self):
        path=env.file_path('runtime')
        env.write_env(path,{'AUTOKOM_JENKINS_USER':'test-user'})
        b=env.backup(path)
        self.assertEqual(b.stat().st_mode & 0o777,0o600)
        self.assertEqual(b.parent.stat().st_mode & 0o777,0o700)

    def test_legacy_current_runtime_wins(self):
        legacy=self.folder/'old-runtime.env'
        legacy.write_text('AUTOKOM_JENKINS_USER=service-account\n')
        env.write_env(env.file_path('runtime'),{'AUTOKOM_JENKINS_USER':'stale-account'})
        with patch.dict(env.OLD_FILES,{'runtime':legacy}),patch.object(env,'select',return_value='Adopter les paramètres du service actuel (recommandé)'):
            env.migrate('runtime')
        self.assertEqual(env.read_env(env.file_path('runtime'))['AUTOKOM_JENKINS_USER'],'service-account')
        self.assertTrue(legacy.is_file())

    def test_reject_non_public_ipv4(self):
        with self.assertRaises(env.EnvError):env._validate('runtime',{'AUTOKOM_INGRESS_IPV4':'172.31.21.188'})

    def test_show_requires_root(self):
        with patch.object(env.os,'geteuid',return_value=1000):
            with self.assertRaises(env.EnvError):env.show('runtime')

class TestMigration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)

    def test_domain_map(self):
        self.assertEqual(domain.destination('backend.auto.ik0m.tech'),'backend.auto.ikom.mg')
        self.assertEqual(domain.destination('dia.ik0m.tech'),'dia.ikom.mg')
        self.assertEqual(domain.destination('ik0m.tech'),'ikom.mg')

    def test_no_unrelated_domains(self):
        for source in ('google.com','ns1.ik0m.tech','infra.ik0m.tech','evil.ikom.mg'):
            with self.subTest(source=source),self.assertRaises(domain.MigrationError):domain.destination(source)

    def test_nginx_reject_complex(self):
        nginx=self.folder/'sites-enabled';nginx.mkdir()
        (nginx/'example').write_text('server {\n server_name dia.ik0m.tech;\n location / { proxy_pass http://127.0.0.1:4000; }\n}\n')
        (nginx/'complex').write_text('server {\n server_name complex.ik0m.tech;\n root /srv/site;\n}\n')
        with patch.object(domain,'NGINX_DIRS',(nginx,)):
            result=domain.discover_nginx()
        self.assertTrue(result['dia.ik0m.tech']['supported'])
        self.assertFalse(result['complex.ik0m.tech']['supported'])

    def test_generated_proxy_config_does_not_include_shell(self):
        result=domain._managed_conf('dia.ikom.mg',4200,ssl=True)
        self.assertIn('proxy_pass http://127.0.0.1:4200',result)
        self.assertIn('ssl_certificate /etc/letsencrypt/live/dia.ikom.mg',result)
        self.assertNotIn('exec',result)

    def test_refuse_cloudflare_collision(self):
        fake=types.SimpleNamespace(records=lambda record_name:[{'name':record_name,'type':'CNAME','content':'x.example'}])
        with self.assertRaises(domain.MigrationError):domain.cloudflare_preflight(fake,'dia.ikom.mg','56.228.86.160')

    def test_new_proxy_cannot_overwrite_unmanaged(self):
        available=self.folder/'available';enabled=self.folder/'enabled'
        available.mkdir();enabled.mkdir()
        (available/'autokom-migrate-dia.ikom.mg.conf').write_text('# ADMIN HAND-WRITTEN\n')
        with patch.object(domain,'SITES_AVAILABLE',available),patch.object(domain,'SITES_ENABLED',enabled):
            with self.assertRaises(domain.MigrationError):domain._commit_nginx('dia.ikom.mg',4200)

    def test_noninteractive_plan_no_changes(self):
        fake={'dia.ik0m.tech':{'port':4200,'supported':True,'sources':['/etc/nginx/sites-enabled/dia']}}
        output=io.StringIO()
        with patch.object(domain,'discover_nginx',return_value=fake),patch.object(domain,'authoritative_ns_ok',return_value=(False,[])),redirect_stdout(output):
            self.assertEqual(domain.main(['migrate','--check']),0)
        self.assertIn('dia.ikom.mg',output.getvalue())

if __name__=='__main__':unittest.main()
