import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import observability_extras as x
import observability_dashboards as d
import observability_docker as docker
import observability_audit as audit
import observability as o

class Merge(unittest.TestCase):
    def test_null_fields_and_idempotence(self):
        for alerting in ('null','{alertmanagers: null}','{alertmanagers: [{static_configs: null}]}'):
            raw='scrape_configs: []\nrule_files: null\nalerting: '+alerting+'\n'
            new=x.merge_prom_extras(raw,alerts=True,node=True,cadvisor=True)
            obj=yaml.safe_load(new)
            self.assertEqual(obj['rule_files'],[str(x.RULES)])
            self.assertEqual(new,x.merge_prom_extras(new,alerts=True,node=True,cadvisor=True))
    def test_localhost_not_duplicated(self):
        raw='scrape_configs: []\nalerting:\n  alertmanagers:\n  - static_configs:\n    - targets: [localhost:9093]\n'
        self.assertEqual(len(yaml.safe_load(x.merge_prom_extras(raw,alerts=True))['alerting']['alertmanagers']),1)
    def test_unknown_shapes_refused(self):
        for tail in ('rule_files: wrong','alerting: []','alerting: {alertmanagers: wrong}'):
            with self.assertRaises(ValueError):x.merge_prom_extras('scrape_configs: []\n'+tail,alerts=True)
    def test_third_party_kept(self):
        raw='scrape_configs: [{job_name: external}]\nrule_files: [other.yml]\nremote_write: [{url: "https://example.test/write"}]\n'
        result=yaml.safe_load(x.merge_prom_extras(raw,alerts=True))
        for key in ('scrape_configs','remote_write'):self.assertEqual(result[key],yaml.safe_load(raw)[key])
        self.assertIn('other.yml',result['rule_files'])
    def test_rules_custom_job_and_absence(self):
        rules=yaml.safe_load(x.alert_rules('ci'))['groups'][0]['rules']
        self.assertEqual(len(rules),9)
        self.assertIn('job="ci"',rules[0]['expr'])
        self.assertIn('absent(',rules[0]['expr'])
        self.assertTrue(all(r['for'] and r['annotations']['summary'] for r in rules))

class Dashboards(unittest.TestCase):
    def test_matching_uid_custom_job(self):
        dashboards=d.dashboards('custom-source','ci')
        self.assertEqual(len({v['uid'] for v in dashboards}),4)
        for dash in dashboards:
            for panel in dash['panels']:
                self.assertEqual(panel['datasource']['uid'],'custom-source')
        self.assertIn('job="ci"',dashboards[1]['panels'][0]['targets'][0]['expr'])
    def test_provision_idempotent_and_refuse_external(self):
        with TemporaryDirectory() as td,patch.object(d,'PROVIDER',Path(td)/'provider.yml'),patch.object(d,'DIRECTORY',Path(td)/'dashboards'):
            self.assertTrue(d.provision(o))
            self.assertFalse(d.provision(o))
            d.PROVIDER.write_text('external: true')
            with self.assertRaises(o.SetupError):d.provision(o)
    def test_api_missing_dashboard_fails(self):
        with patch.object(o,'grafana_api',return_value=(404,'')):
            with self.assertRaises(o.SetupError):d.verify(o,('u','p'))

class Docker(unittest.TestCase):
    def good(self):
        return {'Config':{'Labels':{docker.LABEL:'true'}},'HostConfig':{'PortBindings':{'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'8081'}]}}}
    def test_public_and_unmanaged_rejected(self):
        docker.validate_existing(self.good())
        for value in ({}, {'Config':{'Labels':{docker.LABEL:'true'}},'HostConfig':{'NetworkMode':'host'}}):
            with self.assertRaises(ValueError):docker.validate_existing(value)
    def test_optout_no_actions(self):
        with patch.object(docker,'inspect_existing') as inspect,patch.object(o,'out'):
            docker.ensure_cadvisor(o,SimpleNamespace())
        inspect.assert_not_called()

class Audit(unittest.TestCase):
    def test_unloaded_rules_and_empty_series_not_success(self):
        report=MagicMock()
        def http(port,path):
            if '/targets' in path:return 200,json.dumps({'status':'success','data':{'activeTargets':[{'labels':{'job':'node'},'health':'up'}]}})
            if '/query' in path:return 200,json.dumps({'status':'success','data':{'result':[]}})
            return 200,json.dumps({'status':'success','data':{}})
        audit.audit(report,o.cfg(),http)
        failures=[call.args[1] for call in report.add.call_args_list if call.args[0]=='FAIL']
        self.assertIn('Règles AUTOKOM chargées',failures)
        self.assertIn('node données',failures)
        self.assertIn('Prometheus → Alertmanager',failures)


class RuntimeRules(unittest.TestCase):
    def test_file_presence_is_not_enough(self):
        with patch.object(o,'get_local',return_value=(200,'{"status":"success","data":{"groups":[]}}')),patch.object(x.time,'sleep'):
            with self.assertRaises(o.SetupError):x.verify_loaded_rules(o)
    def test_loaded_rules_accepted(self):
        rules=yaml.safe_load(x.alert_rules())['groups'][0]['rules']
        payload=json.dumps({'status':'success','data':{'groups':[{'rules':[{'name':r['alert'],'health':'ok'} for r in rules]}]}})
        with patch.object(o,'get_local',return_value=(200,payload)):
            x.verify_loaded_rules(o)

if __name__=='__main__':unittest.main()
