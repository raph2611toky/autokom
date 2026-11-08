"""Regression tests: atomic public publication and safe Nginx rollback.

Do not access Docker, Nginx, Certbot or the network.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock

import autokom_internal_ingress as ingress
import autokom_platform_broker as broker

HOST='portfolio.auto.ikom.mg'
ENV='674be29f-4fe2-4d68-b1c9-554e561b8923'
RUN_ID='0bba85bc-c668-480c-8a41-d1b037403ee7'
NAME='autokom-'+ENV.replace('-','')[:20]


class SafeIngressRemoval(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.available=root/'available';self.available.mkdir()
        self.enabled=root/'enabled';self.enabled.mkdir()
        self.source,self.link=ingress._layout(NAME,self.available,self.enabled)
        self.source.write_text('server { listen 127.0.0.1:10000; }\n')
        self.link.symlink_to(self.source)

    def test_detaches_link_before_nginx_test_and_keeps_source_through_reload(self):
        calls=[]
        def execute(cmd, **kw):
            self.assertFalse(self.link.is_symlink(), 'No dangling enabled link during reload')
            self.assertTrue(self.source.is_file(), 'Do not unlink source until reload succeeded')
            calls.append(cmd)
        self.assertTrue(ingress._remove_config(NAME, execute=execute,
                         available=self.available,enabled=self.enabled))
        self.assertEqual(calls,[['nginx','-t'],['systemctl','reload','nginx']])
        self.assertFalse(self.link.exists())
        self.assertFalse(self.source.exists())

    def test_failed_nginx_reload_restores_link_and_keeps_source(self):
        def fail(cmd,**kw):
            if cmd[0]=='systemctl':raise RuntimeError('test reload failure')
        with self.assertRaisesRegex(ingress.IngressError,'AUTOKOM_INGRESS_DETACH_FAILED'):
            ingress._remove_config(NAME, execute=fail,
                                   available=self.available,enabled=self.enabled)
        self.assertTrue(self.source.is_file())
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(self.link.resolve(),self.source)


class HealthAndRollback(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        for folder in ('state','available','enabled'):(root/folder).mkdir()
        for key,dir in [('STATE',root/'state'),('PROXY',root/'available'),('ENABLED',root/'enabled')]:
            p=patch.object(broker,key,dir);p.start();self.addCleanup(p.stop)
        (broker.STATE/ENV).mkdir()
        self.state={'environment_id':ENV,'run_id':RUN_ID,'name':NAME,
                    'network':NAME,'port':31001,'internal_port':8080,
                    'ingress_mode':ingress.MODE,'healthcheck':'/'}
        (broker.STATE/ENV/'current.json').write_text(json.dumps(self.state))

    def test_rollback_detaches_nginx_before_stopping_container(self):
        name='autokom-customer-'+HOST+'.conf'
        conf=broker.PROXY/name
        conf.write_text('old customer proxy')
        link=broker.ENABLED/name
        link.symlink_to(conf)
        (broker.STATE/('domain-owner-'+HOST+'.txt')).write_text(ENV)
        events=[]
        def system(cmd,**kw):
            if cmd[:3]==['docker','rm','-f']:
                self.assertFalse(link.is_symlink(), 'Stop Docker only after public vhost detach')
            events.append(cmd)
        with patch.object(broker,'run',side_effect=system), \
             patch.object(broker.ingress,'remove',side_effect=lambda *a,**k: events.append(['ingress.remove'])):
            self.assertTrue(broker._rollback({'environment_id':ENV,'run_id':RUN_ID})['rolled_back'])
        self.assertFalse(link.is_symlink())
        self.assertFalse(conf.exists())
        self.assertLess(events.index(['systemctl','reload','nginx']),events.index(['docker','rm','-f',NAME]))
        self.assertLess(events.index(['ingress.remove']),events.index(['docker','rm','-f',NAME]))
        self.assertFalse((broker.STATE/ENV/'current.json').exists())

    def test_previous_healthy_version_is_restored_before_container_stop(self):
        previous={**self.state, 'run_id':'f5a3b693-94bf-465d-9d2f-2d0f43dbfafe',
                  'name':'autokom-f5a3b69394bf465d9d2f', 'port':31999}
        (broker.STATE/ENV/'previous.json').write_text(json.dumps(previous))
        name='autokom-customer-'+HOST+'.conf'
        conf=broker.PROXY/name
        conf.write_text('previously staged vhost')
        link=broker.ENABLED/name;link.symlink_to(conf)
        (broker.STATE/('domain-owner-'+HOST+'.txt')).write_text(ENV)
        events=[]
        def system(cmd, **kw):
            if cmd[:3]==['docker','rm','-f']:
                self.assertIn('proxy_pass http://127.0.0.1:31999;',conf.read_text())
                self.assertTrue(link.is_symlink())
            events.append(cmd)
        with patch.object(broker,'run',side_effect=system), \
             patch.object(broker.ingress,'remove',return_value=True):
            report=broker._rollback({'environment_id':ENV,'run_id':RUN_ID})
        self.assertTrue(report['previous_available'])
        self.assertEqual(json.loads((broker.STATE/ENV/'current.json').read_text()),previous)
        self.assertTrue(link.is_symlink())
        self.assertFalse((broker.STATE/ENV/'previous.json').exists())

    def test_invalid_public_vhost_link_refused_before_docker_stop(self):
        other=broker.ENABLED/'unrelated.conf';other.write_text('unrelated')
        name='autokom-customer-'+HOST+'.conf'
        link=broker.ENABLED/name;link.symlink_to(other)
        (broker.STATE/('domain-owner-'+HOST+'.txt')).write_text(ENV)
        with patch.object(broker,'run') as system:
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_ROLLBACK_VHOST_INVALID'):
                broker._rollback({'environment_id':ENV,'run_id':RUN_ID})
            system.assert_not_called()

    def test_dangling_customer_vhost_is_not_silently_adopted(self):
        link=broker.ENABLED/('autokom-customer-'+HOST+'.conf')
        link.symlink_to(broker.PROXY/link.name)
        with patch.object(broker,'_load_state',return_value=self.state), patch.object(broker,'run') as system:
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_DOMAIN_VHOST_DANGLING'):
                broker._proxy_apply(HOST, ENV, managed=True)
            system.assert_not_called()


class StableHttps(TestCase):
    def test_transient_502_recovers_and_makes_get_request(self):
        raw=MagicMock();tls=MagicMock();context=MagicMock()
        raw.__enter__.return_value=raw;tls.__enter__.return_value=tls
        context.wrap_socket.return_value=tls
        def response(status):
            result=MagicMock();result.status=status;return result
        with patch.object(broker.ssl,'create_default_context',return_value=context), \
             patch.object(broker.socket,'create_connection',return_value=raw), \
             patch.object(broker.http.client,'HTTPResponse',side_effect=[response(502),response(200)]), \
             patch.object(broker.time,'sleep') as sleepy:
            broker._verify_customer_https(HOST,'/')
        self.assertEqual(sleepy.call_count,1)
        self.assertEqual(tls.sendall.call_count,2)
        self.assertIn(b'GET / HTTP/1.1',tls.sendall.call_args.args[0])

    def test_persistent_502_rejected_not_marked_success(self):
        raw=MagicMock();tls=MagicMock();ctx=MagicMock()
        raw.__enter__.return_value=raw;tls.__enter__.return_value=tls
        ctx.wrap_socket.return_value=tls
        response=MagicMock();response.status=502
        with patch.object(broker.ssl,'create_default_context',return_value=ctx), \
             patch.object(broker.socket,'create_connection',return_value=raw), \
             patch.object(broker.http.client,'HTTPResponse',return_value=response), \
             patch.object(broker.time,'sleep'):
            with self.assertRaisesRegex(broker.BrokerError,'AUTOKOM_HTTPS_UPSTREAM_FAILED.*502'):
                broker._verify_customer_https(HOST,'/')

    def test_publication_stage_is_only_successful_after_public_https(self):
        source=(Path(__file__).resolve().parents[1]/'platform/backend/apps/deployments/runs/worker.py').read_text()
        tree=ast.parse(source)
        function=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_release_docker_and_proxy')
        text=ast.get_source_segment(source,function)
        self.assertGreater(text.index("stage_key='release').update(status='SUCCESS'"),text.index('activate_managed_domains_for_release(run.environment)'))
