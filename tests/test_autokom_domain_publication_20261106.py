"""Offline DNS+HTTPS safety tests. No real network, Docker or certificates."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import autokom_platform_broker as broker

HOST = 'portfolio.auto.ikom.mg'
ENV = '674be29f-4fe2-4d68-b1c9-554e561b8923'
CONFIG = {
    'AUTOKOM_MANAGED_DOMAIN_SUFFIX':'auto.ikom.mg',
    'CLOUDFLARE_ZONE_NAME':'ikom.mg',
    'CLOUDFLARE_ZONE_ID':'c'*32,
    'CLOUDFLARE_API_TOKEN':'never-show-this-secret',
    'AUTOKOM_INGRESS_IPV4':'56.228.86.160',
}

class DNSProvisionTests(unittest.TestCase):
    def setUp(self):
        self.vars=patch.dict(os.environ,CONFIG)
        self.vars.start()
        self.addCleanup(self.vars.stop)

    def test_managed_domain_creates_cloudflare_a_record(self):
        requests=[]
        def mocked(req,timeout=None):
            requests.append(req)
            data={'success':True,'result':[]}
            return io.BytesIO(json.dumps(data).encode())
        with patch.object(broker.urllib.request,'urlopen',side_effect=mocked):
            result=broker._cloudflare_record(HOST)
        self.assertTrue(result['created'])
        self.assertEqual(len(requests),3)
        self.assertIn('type=CNAME',requests[0].full_url)
        self.assertIn('type=A',requests[1].full_url)
        self.assertEqual(requests[2].get_method(),'POST')
        payload=json.loads(requests[2].data)
        self.assertEqual(payload['name'],HOST)
        self.assertEqual(payload['content'],'56.228.86.160')
        self.assertIs(payload['proxied'],False)

    def test_duplicate_record_is_idempotent(self):
        state={'n':0}
        def mocked(req,timeout=None):
            state['n']+=1
            existing=({'success':True,'result':[{'name':HOST,'content':'56.228.86.160','proxied':False}]}
                      if state['n']==2 else {'success':True,'result':[]})
            return io.BytesIO(json.dumps(existing).encode())
        with patch.object(broker.urllib.request,'urlopen',side_effect=mocked):
            self.assertEqual(broker._cloudflare_record(HOST),{'created':False})
        self.assertEqual(state['n'],2)

    def test_existing_cname_is_not_overwritten(self):
        def mocked(req,timeout=None):
            return io.BytesIO(json.dumps({'success':True,'result':[{'name':HOST,'content':'old.example','proxied':False}]}).encode())
        with patch.object(broker.urllib.request,'urlopen',side_effect=mocked):
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_DOMAIN_DNS_CONFLICT'):
                broker._cloudflare_record(HOST)

    def test_403_is_safe_and_actionable(self):
        with patch.object(broker.urllib.request,'urlopen',side_effect=HTTPError('https://api.cloudflare.com',403,'Forbidden',{},None)):
            with self.assertRaises(broker.BrokerError) as err:
                broker._cloudflare_record(HOST)
        self.assertIn('AUTOKOM_CF_AUTH_DENIED',str(err.exception))
        self.assertNotIn(CONFIG['CLOUDFLARE_API_TOKEN'],str(err.exception))

    def test_refuses_zone_mismatch(self):
        with patch.dict(os.environ,{'CLOUDFLARE_ZONE_NAME':'another-zone.tld'}):
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_CF_ZONE_MISMATCH'):
                broker._cloudflare_record(HOST)

    def test_missing_vhost_is_not_misreported_as_active(self):
        with tempfile.TemporaryDirectory() as d:
            available=Path(d)/'available'; enabled=Path(d)/'enabled';state=Path(d)/'state'
            for item in (available,enabled,state):item.mkdir()
            with patch.object(broker,'PROXY',available),patch.object(broker,'ENABLED',enabled),patch.object(broker,'STATE',state):
                self.assertFalse(broker._domain_inspect({'hostname':HOST,'environment_id':ENV})['active'])
                conf=available/('autokom-customer-'+HOST+'.conf')
                conf.write_text('server_name '+HOST+';\nlisten 443 ssl;\n')
                (enabled/conf.name).symlink_to(conf)
                (state/('domain-owner-'+HOST+'.txt')).write_text('11111111-1111-4111-8111-111111111111')
                with self.assertRaisesRegex(broker.BrokerError,'OWNERSHIP_MISMATCH'):
                    broker._domain_inspect({'hostname':HOST,'environment_id':ENV})

    def test_automatic_service_wiring_and_no_manual_jenkins_rebuild(self):
        root=Path(__file__).resolve().parents[1]
        role=(root/'ansible/roles/platform_runtime/tasks/main.yml').read_text()
        self.assertIn('autokom-domain-reconciler.timer',role)
        command=(root/'platform/backend/apps/deployments/domains/management/commands/autokom_domain_reconcile.py').read_text()
        self.assertIn('reconcile_domains',command)
        code=(root/'platform/backend/apps/deployments/domains/reconciler.py').read_text()
        self.assertIn('activate_managed_domains_for_release(env)',code)
        self.assertIn('domain.inspect',code)
        self.assertNotIn('jenkins.dispatch',code)

if __name__=='__main__':unittest.main()
