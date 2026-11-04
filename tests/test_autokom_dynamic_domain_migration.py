"""Offline regression tests: ZERO actual Cloudflare, Nginx, Certbot or service writes."""
from __future__ import annotations
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_domain_migration as m
import autokom_domain_dns as dns

IP='56.228.86.160'
ZONE='ikom.mg'
HOST='dia.ikom.mg'
ORIGIN=ZONE

class FakeCF:
    def __init__(self,records=()):
        self.items=[{'id':'root','name':ZONE,'type':'A','content':IP,'proxied':True,'ttl':1}] + [dict(r) for r in records]
        self.events=[]
        self.fail_cname_once=False
        self.zone_id='z'*32
    def records(self,record_name=None):
        return [dict(x) for x in self.items if not record_name or x['name']==record_name]
    def create(self,zone,kind,name,value,ttl=120,proxied=False):
        if kind=='CNAME' and self.fail_cname_once:
            self.fail_cname_once=False
            raise ValueError('fake CF refusal')
        if [x for x in self.items if x['name']==name]:
            raise RuntimeError('CNAME/A collision')
        row={'id':str(len(self.events)+1),'type':kind,'name':name,'content':value,'proxied':proxied,'ttl':ttl}
        self.items.append(row);self.events.append(('create',kind,name,value))
        return row
    def delete(self,id):
        row=next(x for x in self.items if x['id']==id)
        self.items.remove(row);self.events.append(('delete',row['name']))
    def request(self,method,path):
        return {'result':{'name':ZONE,'status':'active','name_servers':['kallie.ns.cloudflare.com','justin.ns.cloudflare.com']}}

class DNSPlanner(unittest.TestCase):
    def test_new_dns_is_one_a_and_cname(self):
        cf=FakeCF();p=dns.plan(cf,ZONE,HOST,IP)
        self.assertEqual(p.site_mode,'new')
        dns.ensure(cf,p)
        self.assertEqual([(x['type'],x['name'],x['content']) for x in cf.items],
                         [('A',ZONE,IP),('CNAME',HOST,ZONE)])
        self.assertTrue(all(x['proxied'] for x in cf.items))
    def test_repeated_dns_plan_does_not_duplicate(self):
        cf=FakeCF();dns.ensure(cf,dns.plan(cf,ZONE,HOST,IP))
        self.assertEqual(dns.plan(cf,ZONE,HOST,IP).site_mode,'cname')
        dns.ensure(cf,dns.plan(cf,ZONE,HOST,IP))
        self.assertEqual(len(cf.events),1)
    def test_existing_legacy_a_can_be_reused(self):
        cf=FakeCF([{'id':'a1','name':HOST,'type':'A','content':IP,'proxied':False,'ttl':120}])
        p=dns.plan(cf,ZONE,HOST,IP)
        self.assertEqual(p.site_mode,'legacy_a')
        dns.ensure(cf,p)
        self.assertEqual(cf.records(record_name=HOST)[0]['type'],'A')
        self.assertEqual(cf.records(record_name=ORIGIN)[0]['type'],'A')
    def test_existing_a_can_convert_only_after_explicit_call(self):
        cf=FakeCF([{'id':'a1','name':HOST,'type':'A','content':IP,'proxied':False,'ttl':120}])
        p=dns.plan(cf,ZONE,HOST,IP);dns.ensure(cf,p)
        self.assertEqual(dns.convert_legacy_a(cf,p),'converted')
        self.assertEqual(cf.records(record_name=HOST)[0]['type'],'CNAME')
    def test_conversion_failure_restores_old_a(self):
        cf=FakeCF([{'id':'a1','name':HOST,'type':'A','content':IP,'proxied':False,'ttl':120}])
        p=dns.plan(cf,ZONE,HOST,IP);dns.ensure(cf,p)
        cf.fail_cname_once=True
        with self.assertRaisesRegex(dns.DNSMigrationError,'restauré'):
            dns.convert_legacy_a(cf,p)
        self.assertEqual(cf.records(record_name=HOST)[0]['type'],'A')
    def test_reject_external_dns_or_critical_name(self):
        for hostname in ('evil.invalid','ns1.ikom.mg','infra.ikom.mg','origin.ikom.mg','ikom.mg'):
            with self.subTest(hostname=hostname),self.assertRaises(dns.DNSMigrationError):
                dns.plan(FakeCF(),ZONE,hostname,IP)
    def test_refuse_existing_proxied_alias_or_mismatched_a(self):
        for record in (
            {'id':'3','name':ORIGIN,'type':'A','content':'1.1.1.1','proxied':False},
            {'id':'4','name':HOST,'type':'A','content':'1.1.1.1','proxied':False},
            {'id':'5','name':HOST,'type':'CNAME','content':'other.ikom.mg','proxied':False},
        ):
            with self.subTest(record=record), self.assertRaises(dns.DNSMigrationError):
                dns.plan(FakeCF([record]),ZONE,HOST,IP)
    def test_refuse_adding_second_type_to_same_name(self):
        cf=FakeCF([{'id':'1','name':HOST,'type':'TXT','content':'hello'}])
        with self.assertRaisesRegex(dns.DNSMigrationError,'occupé'):
            dns.plan(cf,ZONE,HOST,IP)

class Orchestration(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        p=patch.object(m,'STATE',self.root/'state');p.start();self.addCleanup(p.stop)
        p=patch.object(m,'ACME',self.root/'acme');p.start();self.addCleanup(p.stop)
    def test_new_site_nginx_first_then_dns_then_https(self):
        cf=FakeCF();events=[]
        def commit(host,port,ssl=False):events.append(('nginx_https' if ssl else 'nginx_http',host,port))
        def create(*a,**kw):
            events.append(('cloudflare',a[1]));return FakeCF.create(cf,*a,**kw)
        # make sure the certificate is seen as already present, so no external Certbot
        with patch.object(m,'_assert_destination_unclaimed'),patch.object(m.socket,'create_connection',return_value=nullcontext()),\
             patch.object(m,'_commit_nginx',side_effect=commit),patch.object(cf,'create',side_effect=create),\
             patch.object(m,'_wait_dns',return_value=True),\
             patch.object(Path,'is_file',return_value=True),patch.object(m,'run',return_value=SimpleNamespace(returncode=0,stdout='200')):
            rc=m.apply_one('dia.ik0m.tech',{'port':8464,'supported':True},IP,cf,'ops@example.com',target=HOST,zone=ZONE,nameservers=['kallie.ns.cloudflare.com'])
        self.assertEqual(rc,'verified')
        self.assertEqual(events[0][0],'nginx_http')
        self.assertEqual([e for e in events if e[0]=='cloudflare'][0][1],'CNAME')
        self.assertEqual(events[-1][0],'nginx_https')
        self.assertTrue((self.root/'state'/f'{HOST}.json').is_file())
    def test_dns_pending_keeps_nginx_http_and_no_certbot(self):
        cf=FakeCF();phases=[]
        with patch.object(m,'_assert_destination_unclaimed'),patch.object(m.socket,'create_connection',return_value=nullcontext()),\
             patch.object(m,'_commit_nginx',side_effect=lambda *a,**k:phases.append(k.get('ssl',False))),\
             patch.object(m,'_wait_dns',return_value=False),patch.object(m,'run',side_effect=AssertionError('no certbot or curl')):
            self.assertEqual(m.apply_one('dia.ik0m.tech',{'port':8464,'supported':True},IP,cf,'ops@example.com',target=HOST,zone=ZONE),'dns_pending')
        self.assertEqual(phases,[False])
        self.assertEqual(cf.records(record_name=HOST)[0]['type'],'CNAME')
    def test_no_writes_if_loopback_service_down(self):
        cf=FakeCF()
        with patch.object(m.socket,'create_connection',side_effect=OSError('offline')),patch.object(m,'_commit_nginx',side_effect=AssertionError('should not write')):
            with self.assertRaisesRegex(m.MigrationError,'indisponible'):
                m.apply_one('dia.ik0m.tech',{'port':8464,'supported':True},IP,cf,'ops@example.com',target=HOST,zone=ZONE)
        self.assertEqual(len(cf.items),1)  # root A only
    def test_authoritative_cname_may_return_only_name(self):
        def fake_run(args,timeout=5):
            return SimpleNamespace(returncode=0,stdout=ORIGIN+'\n' if args[-1]==HOST else IP+'\n')
        with patch.object(m,'run',side_effect=fake_run):
            self.assertTrue(m._dns_authoritative_ready(HOST,IP,['kallie.ns.cloudflare.com'],ORIGIN,proxied=True))
    def test_dynamic_mapping_without_static_service_names(self):
        self.assertEqual(m.destination('api.old.example','old.example','new.example'),'api.new.example')
        self.assertEqual(m.destination('dashboard.api.old.example','old.example','new.example'),'dashboard.api.new.example')
    def test_native_picker_receives_real_inventory(self):
        found={'dia.ik0m.tech':{'supported':True,'port':8464},'other.ik0m.tech':{'supported':False,'port':0}}
        with patch('autokom_env_manager.select',return_value='dia.ik0m.tech → dia.ikom.mg · 127.0.0.1:8464'):
            choice=m._choose_service(found,'ik0m.tech','ikom.mg')
        self.assertEqual(choice[0],'dia.ik0m.tech')
    def test_nginx_https_does_not_downgrade_on_retry(self):
        av=self.root/'available';en=self.root/'enabled';av.mkdir();en.mkdir()
        (av/f'autokom-migrate-{HOST}.conf').write_text(m._managed_conf(HOST,8464,True))
        (en/f'autokom-migrate-{HOST}.conf').symlink_to(av/f'autokom-migrate-{HOST}.conf')
        with patch.object(m,'SITES_AVAILABLE',av),patch.object(m,'SITES_ENABLED',en),patch.object(m,'run',side_effect=AssertionError('no reload expected')):
            m._commit_nginx(HOST,8464,ssl=False)
        self.assertIn('listen 443 ssl',(av/f'autokom-migrate-{HOST}.conf').read_text())

if __name__=='__main__':unittest.main()

class GuidedCLI(unittest.TestCase):
    def test_choose_service_and_full_plan_from_real_discovery(self):
        cf=FakeCF()
        site={'dia.ik0m.tech':{'port':8464,'supported':True,'proxy_kind':'upstream','sources':['/etc/nginx/sites-enabled/dia.ik0m.tech.conf']}}
        calls=[]
        def choose(title,choices,default=1):
            if title.startswith('Service à migrer'):return choices[0]
            if title.startswith('Migrer '):return 'Créer le domaine en parallèle'
            return choices[0]
        with patch.object(m,'discover_nginx',return_value=site),\
             patch.object(m,'authoritative_ns_ok',return_value=(True,['kallie.ns.cloudflare.com.','justin.ns.cloudflare.com.'])),\
             patch.object(m,'cloudflare',return_value=cf),\
             patch.object(m,'_assert_destination_unclaimed'),\
             patch.object(m,'local_ip',return_value=IP),\
             patch.object(m.os,'geteuid',return_value=0),\
             patch.object(m.os,'isatty',return_value=True),\
             patch('autokom_env_manager.select',side_effect=choose),\
             patch('autokom_env_manager.read_env',side_effect=lambda p: {'AUTOKOM_ACME_EMAIL':'ops@example.com'} if 'runtime' in str(p) else {'CLOUDFLARE_ZONE_NAME':ZONE}),\
             patch.object(m,'apply_one',side_effect=lambda *a,**kw:calls.append(kw) or 'verified'),\
             patch.object(m,'ACME',Path(tempfile.mkdtemp())),patch('autokom_domain_progress.LOG_DIR',Path(tempfile.mkdtemp())),\
             patch('builtins.input',side_effect=['','MIGRER']):
            self.assertEqual(m.main(['migrate','--apply']),0)
        self.assertEqual(calls[0]['target'],HOST)
        self.assertEqual(calls[0]['nameservers'],['kallie.ns.cloudflare.com.','justin.ns.cloudflare.com.'])

    def test_explicit_source_and_a_conversion_requires_two_confirmations(self):
        cf=FakeCF([{'id':'a1','name':HOST,'type':'A','content':IP,'proxied':False,'ttl':120}])
        site={'dia.ik0m.tech':{'port':8464,'supported':True,'proxy_kind':'upstream','sources':['/etc/nginx/sites-enabled/dia.ik0m.tech.conf']}}
        opts=[]
        def choose(title,choices,default=1):
            if title.startswith('Migrer '):return 'Créer le domaine en parallèle'
            if title.startswith('Ancien '):return 'Convertir en CNAME vers '+ZONE+' après validation HTTPS'
            return choices[0]
        with patch.object(m,'discover_nginx',return_value=site),patch.object(m,'authoritative_ns_ok',return_value=(True,['kallie.ns.cloudflare.com.','justin.ns.cloudflare.com.'])),\
             patch.object(m,'cloudflare',return_value=cf),patch.object(m,'_assert_destination_unclaimed'),patch.object(m,'local_ip',return_value=IP),\
             patch.object(m.os,'geteuid',return_value=0),patch.object(m.os,'isatty',return_value=True),\
             patch('autokom_env_manager.select',side_effect=choose),\
             patch('autokom_env_manager.read_env',side_effect=lambda p: {'AUTOKOM_ACME_EMAIL':'ops@example.com'} if 'runtime' in str(p) else {'CLOUDFLARE_ZONE_NAME':ZONE}),\
             patch.object(m,'apply_one',side_effect=lambda *a,**kw:opts.append(kw) or 'verified'),patch.object(m,'ACME',Path(tempfile.mkdtemp())),patch('autokom_domain_progress.LOG_DIR',Path(tempfile.mkdtemp())),\
             patch('builtins.input',side_effect=['','MIGRER','CONVERTIR']):
            self.assertEqual(m.main(['migrate','--source','dia.ik0m.tech','--apply']),0)
        self.assertTrue(opts[0]['convert'])
