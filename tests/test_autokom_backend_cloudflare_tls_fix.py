"""Regression: Cloudflare Free deep-domain TLS, safe CNAME->A, Django startup grace."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import autokom_backend_domain as b

P = b.BackendDomainPlan('backend.auto.ik0m.tech','backend.auto.ikom.mg','ikom.mg',
                        8172, '56.228.86.160','admin@example.org','a','https://auto.ik0m.tech')

class CF:
    zone_id = 'valid-zone'
    def __init__(self, fail_a=False, fail_restore=False):
        self.rows = [
            {'id':'apex','type':'A','name':'ikom.mg','content':P.ingress,'proxied':True,'ttl':1},
            {'id':'cname1','type':'CNAME','name':P.target,'content':P.zone,'proxied':True,'ttl':120},
        ]
        self.calls=[]
        self.fail_a=fail_a
        self.fail_restore=fail_restore
    def records(self, record_name=None):
        return [dict(x) for x in self.rows if record_name is None or x['name']==record_name]
    def delete(self, record_id):
        self.calls.append(('delete',record_id))
        self.rows = [x for x in self.rows if x['id'] != record_id]
    def create(self, zone, typ, name, content, ttl, proxied):
        self.calls.append(('create',typ,name,content,proxied))
        if typ=='A' and self.fail_a:raise RuntimeError('Cloudflare write failed')
        if typ=='CNAME' and self.fail_restore:raise RuntimeError('restore failed')
        self.rows.append({'id':'new','type':typ,'name':name,'content':content,'proxied':proxied,'ttl':ttl})
    def request(self,method,path,data=None):
        return {'result':{'name':'ikom.mg','status':'active','name_servers':['a.ns.cloudflare.com']}}

class TestCloudflareTLS(unittest.TestCase):
    def test_multi_level_host_detection(self):
        self.assertTrue(b._is_deep_domain(P.target,P.zone))
        self.assertFalse(b._is_deep_domain('backend.ikom.mg',P.zone))

    def test_create_plan_uses_a_without_overwriting_user_values(self):
        def fake_env(path):
            name=path.name
            if name.startswith('runtime'):
                return {'AUTOKOM_INGRESS_IPV4':P.ingress,'AUTOKOM_ACME_EMAIL':'admin@example.org'}
            if name.startswith('cloudflare'):
                return {'CLOUDFLARE_ZONE_NAME':P.zone}
            return {'FRONTEND_URL':P.frontend,'DB_PASSWORD':'secret-value'}
        with patch.object(b,'_read_state',return_value={'runtime':'systemd','domain':P.legacy,'port':8172}), \
             patch.object(b,'read_env',side_effect=fake_env):
            p=b.create_plan()
            self.assertEqual(p.dns_mode,'a')
            self.assertEqual(b.create_plan(dns_mode='cname').dns_mode,'cname')

    def test_existing_cname_is_allowed_for_explicit_conversion(self):
        cf=CF()
        plan=b._dns_current(cf,P)
        self.assertEqual(plan.site_mode,'cname')
        self.assertIn('préservé',b._dns_ensure(cf,P,plan))
        self.assertEqual(cf.calls,[])

    def test_conversion_happens_only_to_target_and_is_dns_only(self):
        cf=CF()
        self.assertIn('A DNS-only',b._convert_cname_to_a(cf,P))
        self.assertEqual(cf.calls[0],('delete','cname1'))
        self.assertEqual(cf.calls[1],('create','A',P.target,P.ingress,False))
        self.assertEqual([x['type'] for x in cf.rows],['A','A'])

    def test_failed_conversion_restores_cname_proxied(self):
        cf=CF(fail_a=True)
        with self.assertRaisesRegex(b.DomainError,'restauré'):
            b._convert_cname_to_a(cf,P)
        restored=[x for x in cf.rows if x['name']==P.target]
        self.assertEqual(len(restored),1)
        self.assertEqual((restored[0]['type'],restored[0]['proxied']),('CNAME',True))

    def test_health_probe_retries_without_fixed_sleep(self):
        with patch.object(b,'verify_health',side_effect=[False,False,True]) as check, \
             patch.object(b.time,'sleep',return_value=None):
            self.assertTrue(b._wait_for_health(8172,timeout=30))
            self.assertEqual(check.call_count,3)

    def test_health_failure_rolls_back_env_after_real_deadline(self):
        old={'ALLOWED_HOSTS':P.legacy,'CSRF_TRUSTED_ORIGINS':'https://'+P.legacy,
             'DB_PASSWORD':'do-not-print-me'}
        writes=[]
        with patch.object(b,'read_env',return_value=old), patch.object(b,'backup'), \
             patch.object(b,'write_env',side_effect=lambda _,data:writes.append(data)), \
             patch.object(b,'_run_ok'),patch.object(b,'_wait_for_health',return_value=False), \
             patch.object(b.dm,'run',return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(b.DomainError,'30 s'):
                b._apply_backend_env(P)
        self.assertEqual(writes[-1],old)

    def test_apply_resume_from_https_cname_validates_before_conversion(self):
        from tempfile import TemporaryDirectory
        cf=CF()
        with TemporaryDirectory() as folder:
            base=Path(folder)
            cert=base/P.target/'fullchain.pem'
            cert.parent.mkdir(parents=True)
            cert.write_text('present')
            steps=[]
            def note(name,*args,**kwargs):
                steps.append(name)
                return True
            with patch.object(b,'_check_local'), patch.object(b,'Cloudflare',return_value=cf), \
                 patch.object(b,'_check_dns_ns',return_value=['a.ns.cloudflare.com']), \
                 patch.object(b.os,'geteuid',return_value=0), \
                 patch.object(b.os,'isatty',return_value=True), \
                 patch('autokom_env_manager.select',return_value='Ajouter '+P.target+' en parallèle'), \
                 patch('builtins.input',side_effect=['MIGRER','REMPLACER']), \
                 patch.object(b,'_nginx_commit'), \
                 patch.object(b,'_apply_backend_env',return_value=True), \
                 patch.object(b,'_tls_probe',side_effect=lambda *_:note('tls')), \
                 patch.object(b,'_write_state',side_effect=lambda *_:note('state')), \
                 patch.object(b.dm,'_wait_dns',return_value=True), \
                 patch.object(b,'CERTS',base), \
                 patch('autokom_domain_progress.LOG_DIR',base/'logs'):
                self.assertEqual(b._apply(P),0)
            self.assertEqual(steps,['tls','state'])
            self.assertEqual(cf.calls[0],('delete','cname1'))
            self.assertEqual(cf.calls[1][1],'A')

if __name__=='__main__': unittest.main()
