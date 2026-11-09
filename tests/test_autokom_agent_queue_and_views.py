"""Bounded queueing and isolated Jenkins execution contract."""
import importlib.util
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
def load(path, name):
    spec=importlib.util.spec_from_file_location(name, ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

broker=load('scripts/lib/autokom_platform_broker.py','broker_queue_hardening')
admin=load('scripts/lib/autokom_runtime_admin.py','admin_queue_hardening')


def nodes(*values):
    return json.dumps({'computer':list(values)}).encode()

def agent(online=True,label='autokom-isolated',name='autokom-builder-01'):
    return {'displayName':name, 'offline':not online,'temporarilyOffline':False,
            'numExecutors':2,'assignedLabels':[{'name':label}]}

class AgentQueueTests(TestCase):
    def test_refuses_missing_or_offline_agent(self):
        for payload in (nodes(),nodes(agent(online=False)),nodes(agent(label='public')),
                        nodes(agent(name='Jenkins'))):
            with self.subTest(payload=payload),patch.object(broker,'jenkins_http',return_value=(200,{},payload)):
                with self.assertRaises(broker.BrokerError):broker._isolated_agent_available()

    def test_accepts_online_isolated_agent_only(self):
        with patch.object(broker,'jenkins_http',return_value=(200,{},nodes(agent()))):
            self.assertTrue(broker._isolated_agent_available())

    def test_dispatch_cannot_enqueue_when_agent_unavailable(self):
        with patch.object(broker,'_isolated_agent_available',side_effect=broker.BrokerError('missing')):
            with patch.object(broker,'jenkins_http') as http:
                with self.assertRaises(broker.BrokerError):
                    broker.jenkins_action('jenkins.dispatch',{'run_id':'12345678-1234-1234-1234-123456789abc'})
                http.assert_not_called()

    def test_admin_reads_agent_status(self):
        with patch.object(admin,'jenkins_request',return_value=(200,nodes(agent()))) as req:
            self.assertEqual(admin.isolated_agent_status({}), (True,'online'))
            self.assertIn('computer',req.call_args.args[2])

    def test_list_view_xml_only_matches_autokom_job(self):
        doc=ET.fromstring(admin.jenkins_view_xml('autokom-client-deploy'))
        self.assertEqual(doc.findtext('name'),'AUTOKOM-Clients')
        self.assertEqual(doc.findtext('includeRegex'),r'^autokom\-client\-deploy$')

    def test_queue_has_bounded_timeout_in_worker(self):
        text=(ROOT/'platform/backend/apps/deployments/runs/worker.py').read_text()
        self.assertIn("run.external_run_id.startswith('Q:')",text)
        self.assertIn('timedelta(minutes=8)',text)
        self.assertIn('client.cancel(run.external_run_id)',text)
