"""Safe dynamic GCP Cloudflare Jenkins A reconciliation, no live API calls."""
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import services
import autokom_dns_cloudflare as dns
from autokom_dns_core import DNSError

ZONE_ID='f'*32
SAMPLE={'CLOUDFLARE_ZONE_ID':ZONE_ID,'CLOUDFLARE_API_TOKEN':'secret-not-for-logs','CLOUDFLARE_ZONE_NAME':'ikom.mg'}
HOST='jenkins.ikom.mg'
IP='136.116.89.163'

class FakeCF(dns.Cloudflare):
    def __init__(self,records=None,conf=None):
        super().__init__(conf or SAMPLE)
        self.items=list(records or [])
        self.actions=[]
    def request(self,method,path,data=None,params=None):
        self.actions.append((method,path,data))
        if path == '/zones/'+ZONE_ID:
            return {'result':{'name':'ikom.mg'}}
        if method=='GET' and path.endswith('/dns_records'):
            return {'result':list(self.items),'result_info':{'total_pages':1}}
        if method=='PATCH':
            self.items[0]['content']=data['content']
            return {'result':self.items[0]}
        if method=='POST':
            self.items.append({**data,'id':'a'*32})
            return {'result':self.items[-1]}
        raise AssertionError((method,path,data))

class DNSSyncTests(unittest.TestCase):
    def test_create_only_jenkins_a_and_idempotent(self):
        cf=FakeCF([{'id':'b'*32,'name':'other.ikom.mg','type':'A','content':'1.1.1.1'}])
        self.assertEqual(cf.ensure_jenkins_a('ikom.mg',HOST,IP),'created')
        self.assertEqual(cf.ensure_jenkins_a('ikom.mg',HOST,IP),'unchanged')
        self.assertEqual(len([m for m,p,d in cf.actions if m=='POST']),1)
        self.assertEqual(cf.items[0]['content'],'1.1.1.1')
        self.assertFalse(cf.items[1]['proxied'])

    def test_patch_only_content_preserve_proxy_ttl(self):
        current={'id':'b'*32,'name':HOST,'type':'A','content':'56.228.86.160','ttl':300,'proxied':True}
        cf=FakeCF([current])
        self.assertEqual(cf.ensure_jenkins_a('ikom.mg',HOST,IP),'updated')
        patches=[d for m,p,d in cf.actions if m=='PATCH']
        self.assertEqual(patches,[{'content':IP}])
        self.assertEqual(cf.items[0]['ttl'],300)
        self.assertTrue(cf.items[0]['proxied'])

    def test_preserve_cname_aaaa_ns_and_apex(self):
        for kind in ('CNAME','AAAA','NS'):
            cf=FakeCF([{'name':HOST,'type':kind,'content':'a.example.com'}])
            with self.assertRaises(DNSError): cf.ensure_jenkins_a('ikom.mg',HOST,IP)
            self.assertFalse(any(m in ('POST','PATCH','DELETE') for m,p,d in cf.actions))
        with self.assertRaises(DNSError): FakeCF().ensure_jenkins_a('ikom.mg','ikom.mg',IP)
        with self.assertRaises(DNSError): FakeCF().ensure_jenkins_a('ikom.mg','ns1.ikom.mg',IP)
        with self.assertRaises(DNSError): FakeCF().ensure_jenkins_a('ikom.mg','jenkins.otherzone.org',IP)

    def test_duplicate_and_bad_id_fail_closed(self):
        rec={'name':HOST,'type':'A','content':'56.228.86.160','id':'b'*32}
        with self.assertRaises(DNSError): FakeCF([rec,rec]).ensure_jenkins_a('ikom.mg',HOST,IP)
        with self.assertRaises(DNSError): FakeCF([{**rec,'id':'../oops'}]).ensure_jenkins_a('ikom.mg',HOST,IP)

    def test_no_extra_zone_read_permission_required(self):
        cf=FakeCF()
        cf.ensure_jenkins_a('ikom.mg', HOST, IP)
        self.assertFalse(any(method=='GET' and path=='/zones/'+ZONE_ID
                             for method,path,data in cf.actions))

    def test_gcp_metadata_uses_no_proxy_and_header(self):
        response=mock.MagicMock()
        response.__enter__.return_value=response
        response.headers={'Metadata-Flavor':'Google'}
        response.read.return_value=IP.encode()
        opener=mock.Mock()
        opener.open.return_value=response
        with mock.patch.object(services.urllib.request,'build_opener',return_value=opener) as build:
            self.assertEqual(services.gcp_public_ipv4(),IP)
        self.assertIsInstance(build.call_args.args[0],services.urllib.request.ProxyHandler)
        req=opener.open.call_args.args[0]
        self.assertEqual(req.get_header('Metadata-flavor'),'Google')
        self.assertIn('metadata.google.internal',req.full_url)

    def test_gcp_metadata_requires_response_header(self):
        response=mock.MagicMock()
        response.__enter__.return_value=response
        response.headers={}
        response.read.return_value=IP.encode()
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(services.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(RuntimeError): services.gcp_public_ipv4()

    def test_no_cloudflare_file_preserves_external_dns(self):
        with mock.patch.object(dns,'CONFIG',Path('/unlikely-nonexistent/autokom/cloudflare-dns.env')):
            with redirect_stdout(io.StringIO()):
                self.assertIsNone(services.sync_cloudflare_jenkins_domain(HOST))

    def test_wrong_zone_skips_metadata_and_api(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cloudflare-dns.env'
            p.write_text('\n'.join(f'{k}={v}' for k,v in SAMPLE.items()))
            p.chmod(0o600)
            with mock.patch.object(dns,'CONFIG',p),mock.patch.object(services,'gcp_public_ipv4',side_effect=AssertionError('should skip')):
                with redirect_stdout(io.StringIO()):
                    self.assertIsNone(services.sync_cloudflare_jenkins_domain('jenkins.ik0m.tech'))

    def test_public_dns_requires_both_resolvers(self):
        def success(args,**kwargs): return types.SimpleNamespace(stdout=IP+'\n',returncode=0)
        with mock.patch.object(services.subprocess,'run',side_effect=success),redirect_stdout(io.StringIO()):
            services.wait_for_public_dns(HOST,IP,attempts=1,delay=0)
        def stale(args,**kwargs):
            return types.SimpleNamespace(stdout=('56.228.86.160\n' if '@1.1.1.1' in args else IP+'\n'),returncode=0)
        with mock.patch.object(services.subprocess,'run',side_effect=stale),redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError): services.wait_for_public_dns(HOST,IP,attempts=1,delay=0)

    def test_check_mode_will_not_run_dns_reconcile(self):
        source=(ROOT/'scripts/lib/services.py').read_text()
        self.assertIn('target_ip = sync_cloudflare_jenkins_domain(domain)',source)
        self.assertLess(source.index("if args.mode == 'check':\n        say('Simulation terminée"),source.index('configure_proxy(args)'))

if __name__=='__main__': unittest.main()

class JenkinsURLMigrationTests(unittest.TestCase):
    def test_only_managed_script_allows_previous_url(self):
        old='https://jenkins.ik0m.tech/'
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'90-autokom-location.groovy'
            f.write_text("// " + services.MANAGED + "\nlocation.setUrl('"+old+"')\n")
            self.assertEqual(services.managed_jenkins_previous_url(old,f),old)
            self.assertIsNone(services.managed_jenkins_previous_url('https://other.example.com/',f))
            f.write_text("// a custom admin initializer\nlocation.setUrl('"+old+"')\n")
            self.assertIsNone(services.managed_jenkins_previous_url(old,f))
    def test_never_use_symlink_as_managed_location(self):
        old='https://jenkins.ik0m.tech/'
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);f=base/'file';f.write_text("// " + services.MANAGED + "\nlocation.setUrl('"+old+"')\n")
            link=base/'link';link.symlink_to(f)
            self.assertIsNone(services.managed_jenkins_previous_url(old,link))

class ManagedLocationFlowTests(unittest.TestCase):
    def test_reconfigure_autokom_jenkins_url_only_with_previous_managed_file(self):
        from pathlib import Path as RealPath
        with tempfile.TemporaryDirectory() as d:
            base=RealPath(d)
            xml=base/'jenkins.model.JenkinsLocationConfiguration.xml'
            init=base/'90-autokom-location.groovy'
            xml.write_text('<root><jenkinsUrl>https://jenkins.ik0m.tech/</jenkinsUrl></root>')
            init.write_text("// " + services.MANAGED + "\nlocation.setUrl('https://jenkins.ik0m.tech/')\n")
            def location_path(value):
                original=str(value)
                if original.endswith('jenkins.model.JenkinsLocationConfiguration.xml'): return xml
                if original.endswith('90-autokom-location.groovy'): return init
                return RealPath(value)
            previous=services.os.environ.get('AUTOKOM_JENKINS_URL_CHANGED')
            try:
                with mock.patch.object(services,'Path',side_effect=location_path), \
                     mock.patch.object(services,'run',return_value=types.SimpleNamespace(returncode=0)), \
                     redirect_stdout(io.StringIO()):
                    options=types.SimpleNamespace(domain=HOST,tls='certbot')
                    services.configure_jenkins_url(options)
                content=init.read_text()
                self.assertIn('https://jenkins.ik0m.tech/',content)
                self.assertIn('https://jenkins.ikom.mg/',content)
                self.assertIn("current == 'https://jenkins.ik0m.tech/'",content)
                self.assertEqual(services.os.environ.get('AUTOKOM_JENKINS_URL_CHANGED'),'1')
            finally:
                if previous is None: services.os.environ.pop('AUTOKOM_JENKINS_URL_CHANGED',None)
                else: services.os.environ['AUTOKOM_JENKINS_URL_CHANGED']=previous

class PublicationOrderingTests(unittest.TestCase):
    def test_jenkins_url_changes_only_after_successful_proxy_tls(self):
        code=(ROOT/'scripts/lib/services.py').read_text()
        main=code[code.index('def main():'):]
        self.assertLess(main.index('configure_jenkins_listen(args)'),main.index('configure_proxy(args)'))
        self.assertLess(main.index('configure_proxy(args)'),main.index('configure_jenkins_url(args)'))
