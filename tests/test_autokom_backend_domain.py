"""Backend-only domain migration: safe DNS, Nginx, Django, and restart behavior."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import autokom_backend_domain as bd
import autokom_domain_dns as dd

P=bd.BackendDomainPlan('backend.auto.ik0m.tech','backend.auto.ikom.mg',
                       'ikom.mg',8172,'56.228.86.160','admin@example.org','cname',
                       'https://auto.ik0m.tech')

class CloudflareStub:
    def __init__(self):
        self.events=[]
        self.rows=[{'id':'apex','type':'A','name':'ikom.mg','content':P.ingress,'proxied':True,'ttl':1}]
    def records(self,record_name=None):
        return [dict(x) for x in self.rows if record_name is None or x['name']==record_name]
    def create(self,zone,typ,name,value,ttl=120,proxied=False):
        self.events.append(('create',typ,name,value,proxied))
        self.rows.append({'id':'new','type':typ,'name':name,'content':value,'proxied':proxied})
    def request(self,method,path,data=None):
        return {'result':{'name':'ikom.mg','status':'active','name_servers':['a.ns.cloudflare.com','b.ns.cloudflare.com']}}
    zone_id='mock-zone'

class BackendDomainTests(unittest.TestCase):
    def test_default_migration_is_derived_not_hardcoded(self):
        self.assertEqual(bd._derive('backend.auto.ik0m.tech','ikom.mg'),P.target)
        self.assertEqual(bd._derive('backend.project.ik0m.tech','new.mg'), 'backend.project.new.mg')
    def test_other_zone_and_dns_names_refused(self):
        for target in ('ns1.ikom.mg','backend.auto.ik0m.tech','backend.othersite.com',
                       'backend.infra.ikom.mg','backend.origin.ikom.mg'):
            with self.subTest(target=target), self.assertRaises(bd.DomainError):
                bd._derive(P.legacy,P.zone,target)
    def test_http_vhost_is_isolated_and_uses_actual_port(self):
        conf=bd._nginx_config(P,tls=False)
        self.assertIn('server_name '+P.target+';',conf)
        self.assertIn('127.0.0.1:8172',conf)
        self.assertIn('location /static/',conf)
        self.assertIn('location ^~ /.well-known/acme-challenge/',conf)
        self.assertNotIn('ssl_certificate',conf)
        self.assertNotIn('proxy_pass http://56.228.',conf)
    def test_https_config_has_certificate_and_redirect(self):
        conf=bd._nginx_config(P,tls=True)
        self.assertIn('/etc/letsencrypt/live/'+P.target+'/fullchain.pem',conf)
        self.assertIn('308 https://$host$request_uri;',conf)
        self.assertIn('listen 443 ssl;',conf)
        self.assertNotIn('backend.auto.ik0m.tech;',conf)
    def test_systemd_must_use_centralized_env_file(self):
        with patch.object(bd.dm,'run',return_value=SimpleNamespace(returncode=0,stdout='/etc/autokom/platform-backend.env')):
            with self.assertRaises(bd.DomainError):bd._assert_active_environment()
        with patch.object(bd.dm,'run',return_value=SimpleNamespace(returncode=0,stdout='/etc/autokom/secrets/backend.env')):
            bd._assert_active_environment()
    def test_django_values_preserve_all_sensitive_and_frontend(self):
        env={'ALLOWED_HOSTS':'backend.auto.ik0m.tech,localhost',
             'CSRF_TRUSTED_ORIGINS':'https://backend.auto.ik0m.tech,https://auto.ik0m.tech',
             'CORS_ALLOWED_ORIGINS':'https://auto.ik0m.tech',
             'FRONTEND_URL':'https://auto.ik0m.tech',
             'DB_PASSWORD':'do-not-print-me',
             'CLIENT_JWT_SIGNING_KEY':'do-not-print-me',
             'GITHUB_APP_CALLBACK_URL':'https://backend.auto.ik0m.tech/api/projects/git/github/callback/'}
        with patch.object(bd,'read_env',return_value=env):
            old,new=bd._backend_values(P)
        self.assertIsNot(old,new)
        for key in ('DB_PASSWORD','CLIENT_JWT_SIGNING_KEY','GITHUB_APP_CALLBACK_URL',
                    'CORS_ALLOWED_ORIGINS','FRONTEND_URL'):
            self.assertEqual(new[key],env[key])
        self.assertIn(P.target,new['ALLOWED_HOSTS'])
        self.assertIn('https://'+P.target,new['CSRF_TRUSTED_ORIGINS'])
        with patch.object(bd,'read_env',return_value=new):
            _,again=bd._backend_values(P)
        self.assertEqual(new,again)
    def test_cname_only_creates_record_no_origin(self):
        cf=CloudflareStub()
        plan=bd._dns_current(cf,P)
        self.assertEqual(bd._dns_ensure(cf,P,plan),'CNAME créé vers le domaine racine')
        self.assertEqual(cf.events,[('create','CNAME',P.target,'ikom.mg',True)])
    def test_existing_a_not_destroyed(self):
        cf=CloudflareStub()
        cf.rows.append({'id':'legacy','type':'A','name':P.target,'content':P.ingress,'proxied':False})
        plan=bd._dns_current(cf,P)
        self.assertEqual(plan.site_mode,'legacy_a')
        bd._dns_ensure(cf,P,plan)
        self.assertEqual(cf.events,[])
    def test_target_dns_conflict_refused(self):
        cf=CloudflareStub()
        cf.rows.append({'id':'other','type':'A','name':P.target,'content':'1.2.3.4','proxied':False})
        with self.assertRaises(bd.DomainError):bd._dns_current(cf,P)
    def test_domain_state_preserves_original_source(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)/'state.json'
            state.write_text(json.dumps({'runtime':'systemd','domain':P.legacy,'port':8172}),encoding='utf8')
            with patch.object(bd,'STATE_FILE',state):
                bd._write_state(P)
                bd._write_state(P)
            result=json.loads(state.read_text())
            self.assertEqual(result['domain'],P.legacy)
            self.assertEqual(result['domain_aliases'],[P.target])
            self.assertEqual(result['preferred_domain'],P.target)
            self.assertEqual(state.stat().st_mode & 0o777,0o600)
    def test_nginx_custom_file_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            avail=root/'available';ena=root/'enabled';avail.mkdir();ena.mkdir()
            (avail / bd._site_name(P.target)).write_text('# User-maintained custom vhost\n',encoding='utf8')
            with patch.object(bd,'AVAILABLE',avail),patch.object(bd,'ENABLED',ena):
                with self.assertRaises(bd.DomainError):bd._nginx_commit(P,tls=True)
    def test_https_is_never_downgraded_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);avail=root/'available';ena=root/'enabled';avail.mkdir();ena.mkdir()
            entry=avail/bd._site_name(P.target)
            entry.write_text(bd._nginx_config(P,tls=True),encoding='utf8')
            (ena/entry.name).symlink_to(entry)
            with patch.object(bd,'AVAILABLE',avail),patch.object(bd,'ENABLED',ena),\
                 patch.object(bd,'_run_ok',side_effect=AssertionError('must not run')):
                self.assertFalse(bd._nginx_commit(P,tls=False))
                self.assertEqual(entry.read_text(),bd._nginx_config(P,tls=True))
    def test_rollback_on_nginx_validation_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);avail=root/'available';ena=root/'enabled';avail.mkdir();ena.mkdir()
            with patch.object(bd,'AVAILABLE',avail),patch.object(bd,'ENABLED',ena),\
                 patch.object(bd,'_check_claim'),patch.object(bd,'_run_ok',side_effect=bd.DomainError('nginx failed')),\
                 patch.object(bd.dm,'run',return_value=SimpleNamespace(returncode=0)):
                with self.assertRaises(bd.DomainError):bd._nginx_commit(P,tls=False)
            self.assertFalse((avail/bd._site_name(P.target)).exists())
            self.assertFalse((ena/bd._site_name(P.target)).exists())
    def test_backend_env_rollback_on_restart_failure(self):
        before={'ALLOWED_HOSTS':'backend.auto.ik0m.tech','DB_PASSWORD':'secret',
                'CSRF_TRUSTED_ORIGINS':'https://backend.auto.ik0m.tech'}
        written=[]
        with patch.object(bd,'read_env',return_value=before),patch.object(bd,'backup'),\
             patch.object(bd,'write_env',side_effect=lambda _,data:written.append(data)),\
             patch.object(bd,'_run_ok',side_effect=bd.DomainError('service failure')),\
             patch.object(bd.dm,'run',return_value=SimpleNamespace(returncode=0)):
            with self.assertRaises(bd.DomainError):bd._apply_backend_env(P)
        self.assertEqual(written[-1],before)
        self.assertEqual(len(written),2)
    def test_https_uses_local_sni_and_health_path(self):
        calls=[]
        with patch.object(bd,'_run_ok',side_effect=lambda argv,timeout=18: calls.append(argv) or SimpleNamespace(stdout='200',returncode=0)):
            self.assertTrue(bd._tls_probe(P))
        self.assertIn(P.target+':443:127.0.0.1',calls[0])
        self.assertIn('/api/health/',calls[0][-1])
        self.assertNotIn('--insecure',calls[0])
    def test_check_is_readonly_and_no_restart(self):
        cf=CloudflareStub()
        with patch.object(bd,'create_plan',return_value=P),patch.object(bd,'_check_local'),\
             patch.object(bd,'Cloudflare',return_value=cf),patch.object(bd,'_check_dns_ns',return_value=['a.ns.cloudflare.com']),\
             patch.object(bd,'_nginx_commit',side_effect=AssertionError('must not edit')),\
             patch.object(bd,'_apply_backend_env',side_effect=AssertionError('must not edit')):
            self.assertEqual(bd.main(['--check']),0)
    def test_password_protection_is_wired_to_cli(self):
        cli=(Path(__file__).resolve().parents[1]/'scripts/lib/autokom_cli.py').read_text()
        self.assertIn('backend_domain_write',cli)
        self.assertIn('backend_domain_write or dns_write',cli)
    def test_apply_resumes_without_reissuing_cert(self):
        import autokom_domain_progress as progress
        from autokom_env_manager import select
        cf=CloudflareStub()
        cf.rows.append({'id':'already','type':'CNAME','name':P.target,
                        'content':P.zone,'proxied':True})
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            cert=base/P.target/'fullchain.pem'
            cert.parent.mkdir(parents=True)
            cert.write_text('test-placeholder')
            calls=[]
            with patch.object(bd,'_check_local'), \
                 patch.object(bd,'Cloudflare',return_value=cf), \
                 patch.object(bd,'_check_dns_ns',return_value=['a.ns.cloudflare.com','b.ns.cloudflare.com']), \
                 patch.object(bd.os,'geteuid',return_value=0), \
                 patch.object(bd.os,'isatty',return_value=True), \
                 patch('autokom_env_manager.select',return_value='Ajouter '+P.target+' en parallèle'), \
                 patch('builtins.input',return_value='MIGRER'), \
                 patch.object(bd,'_nginx_commit',side_effect=lambda plan,tls:calls.append(('nginx',tls))), \
                 patch.object(bd,'_dns_ensure',return_value='Enregistrement existant'), \
                 patch.object(bd.dm,'_wait_dns',return_value=True), \
                 patch.object(bd,'CERTS',base), \
                 patch.object(progress,'LOG_DIR',base/'logs'), \
                 patch.object(bd,'_apply_backend_env',return_value=False), \
                 patch.object(bd,'_tls_probe',return_value=True), \
                 patch.object(bd,'_write_state'):
                self.assertEqual(bd._apply(P),0)
            self.assertEqual(calls,[('nginx',False),('nginx',True)])

    def test_apply_cancel_does_not_modify_anything(self):
        cf=CloudflareStub()
        with patch.object(bd,'_check_local'), \
             patch.object(bd,'Cloudflare',return_value=cf), \
             patch.object(bd,'_check_dns_ns',return_value=['a.ns.cloudflare.com']), \
             patch.object(bd.os,'geteuid',return_value=0),patch.object(bd.os,'isatty',return_value=True), \
             patch('autokom_env_manager.select',return_value='Annuler · conserver le domaine actuel'), \
             patch.object(bd,'_nginx_commit',side_effect=AssertionError('must not edit')), \
             patch.object(bd,'_apply_backend_env',side_effect=AssertionError('must not edit')):
            self.assertEqual(bd._apply(P),0)
            self.assertEqual(cf.events,[])

if __name__=='__main__': unittest.main()
