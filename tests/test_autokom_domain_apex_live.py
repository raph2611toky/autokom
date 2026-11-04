"""Static/controlled checks, no Cloudflare/DNS/Nginx production side effects."""
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import autokom_domain_dns as dns
import autokom_domain_migration as m
import autokom_domain_progress as live

ZONE='ikom.mg'; IP='56.228.86.160'; TARGET='dia.ikom.mg'
class CF:
    def __init__(self,root_proxy=True):
        self.rows=[{'id':'a','type':'A','name':ZONE,'content':IP,'proxied':root_proxy,'ttl':1}]
        self.events=[]
    def records(self,record_name=None):return [dict(r) for r in self.rows if not record_name or r['name']==record_name]
    def create(self,zone,kind,name,content,ttl=120,proxied=False):
        self.events.append(('create',kind,name,content,proxied))
        self.rows.append({'id':'x'+str(len(self.events)),'type':kind,'name':name,'content':content,'proxied':proxied,'ttl':ttl})
    def delete(self,rid):
        self.events.append(('delete',rid))
        self.rows=[r for r in self.rows if r['id']!=rid]

class ApexDNSTests(TestCase):
    def test_no_additional_origin_a_and_apex_unchanged(self):
        cf=CF()
        dns.ensure(cf,dns.plan(cf,ZONE,TARGET,IP))
        self.assertEqual(len(cf.rows),2)
        self.assertEqual(cf.rows[0]['name'],ZONE)
        self.assertEqual(cf.rows[1]['content'],ZONE)
        self.assertEqual(cf.rows[1]['type'],'CNAME')
        self.assertTrue(cf.rows[1]['proxied'])
    def test_apex_proxied_clarified(self):
        for is_proxied in (False,True):
            cf=CF(is_proxied)
            p=dns.plan(cf,ZONE,TARGET,IP)
            self.assertEqual(p.site_proxied,is_proxied)
    def test_wrong_apex_does_not_mutate(self):
        cf=CF();cf.rows[0]['content']='8.8.8.8'
        with self.assertRaises(dns.DNSMigrationError):dns.ensure(cf,dns.plan(cf,ZONE,TARGET,IP))
        self.assertEqual(cf.events,[])
    def test_legacy_a_preserved_until_conversion(self):
        cf=CF();cf.rows.append({'id':'old','name':TARGET,'type':'A','content':IP,'proxied':False,'ttl':120})
        p=dns.plan(cf,ZONE,TARGET,IP)
        self.assertEqual(dns.ensure(cf,p),'legacy_a')
        self.assertEqual(cf.events,[])
        dns.convert_legacy_a(cf,p)
        self.assertEqual(cf.rows[-1]['content'],ZONE)
        self.assertEqual(cf.rows[-1]['type'],'CNAME')
    def test_old_origin_never_deleted_when_referenced(self):
        cf=CF();cf.rows.append({'id':'or','name':'origin.ikom.mg','type':'A','content':IP,'proxied':False})
        cf.rows.append({'id':'ref','name':'s.ikom.mg','type':'CNAME','content':'origin.ikom.mg','proxied':False})
        old,refs=dns.inspect_obsolete_origin(cf,ZONE,IP)
        self.assertEqual(refs,['s.ikom.mg'])
        self.assertEqual(old['id'],'or')
    def test_https_probe_never_hairpins_public_ip(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        cf=CF();calls=[]
        def fake_run(args,timeout=30):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout='200')
        with tempfile.TemporaryDirectory() as td,patch.object(m,'STATE',Path(td)/'state'),patch.object(m,'ACME',Path(td)/'acme'),\
             patch.object(m.socket,'create_connection',return_value=nullcontext()),patch.object(m,'_assert_destination_unclaimed'),\
             patch.object(m,'_commit_nginx'),patch.object(m,'_wait_dns',return_value=True),\
             patch.object(m,'run',side_effect=fake_run),patch.object(Path,'is_file',return_value=True):
            self.assertEqual(m.apply_one('dia.ik0m.tech',{'supported':True,'port':8464},IP,cf,'ops@example.test',target=TARGET),'verified')
        curl=[c for c in calls if c[0]=='curl']
        self.assertEqual(len(curl),1)
        self.assertIn(TARGET+':443:127.0.0.1',curl[0])
        self.assertNotIn(TARGET+':443:'+IP,curl[0])
    def test_command_log_stream_and_live_preview(self):
        with tempfile.TemporaryDirectory() as td,patch.object(live,'LOG_DIR',Path(td)):
            with live.LiveDomainProgress() as progress:
                progress.label='Exécution fictive'
                result=progress.call([sys.executable,'-u','-c',
                                      "print('step 1');print('step 2');print('step 3')"],timeout=5)
                self.assertEqual(result.returncode,0)
                self.assertIn('step 3',result.stdout)
                self.assertIn('step 3',progress._lines())
                self.assertEqual(progress.log_path.stat().st_mode & 0o777,0o600)
    def test_runtime_log_private_and_sanitized(self):
        with tempfile.TemporaryDirectory() as td,patch.object(live,'LOG_DIR',Path(td)):
            with live.LiveDomainProgress() as progress:
                progress.note('System: test ok')
                progress.note('Authorization: secret-value')
                lines=progress._lines()
                self.assertIn('System: test ok',lines)
                self.assertNotIn('Authorization: secret-value',lines)
                self.assertEqual(progress.log_path.stat().st_mode & 0o777,0o600)
