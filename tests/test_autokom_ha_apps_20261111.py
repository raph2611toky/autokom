"""Tests isolés : aucune connexion GCP / DNS ni service modifié."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_ha_apps.py'
spec = importlib.util.spec_from_file_location('autokom_ha_apps_under_test', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ENV = '94ff7746-5db4-40ee-b3cd-6f2e6ea7e1d0'
RUN = '593a3663-e256-4bf6-830a-8f8bb8228490'
NODE_A = 'autokom-ha-web-6j95'
NODE_B = 'autokom-ha-web-km01'
MIG = [{'name': NODE_A, 'zone': 'us-central1-a', 'private_ip': '10.128.0.13', 'health': 'HEALTHY'},
       {'name': NODE_B, 'zone': 'us-central1-f', 'private_ip': '10.128.0.12', 'health': 'HEALTHY'}]
ENTRY = {'environment_id': ENV, 'run_id': RUN, 'hosts': ['ladyportfolio.auto.ikom.mg']}
DECISION = {'environment': ENV, 'run_id': RUN, 'ha': True, 'status': 'HTTP_LB_READY_NOT_HTTPS',
            'workers': [{'name': NODE_A, 'zone': 'us-central1-a'}, {'name': NODE_B, 'zone': 'us-central1-f'}],
            'healthy_replicas': 2}


class HaAppsTests(unittest.TestCase):
    def full(self, *, report=None, health='HEALTHY', zones=None):
        worker = list(MIG)
        if zones:
            worker = [dict(MIG[0]), dict(MIG[1], zone=zones[1])]
        data = {NODE_A: [dict(ENTRY)], NODE_B: [dict(ENTRY)]}
        statuses = {(n['name'], ENV, RUN): health for n in worker}
        return module.assemble(worker, report or {'applications': [DECISION], 'cloud_https_ready': False},
                               data, statuses)

    def test_two_vm_healthy_without_app_health_is_not_ha(self):
        result = self.full(health='INCONNU')
        self.assertEqual(result['applications'][0]['healthy_app_replicas'], 0)
        self.assertEqual(result['applications'][0]['ha_status'], 'NON_VALIDEE')

    def test_two_app_replicas_http_only_are_not_public_ha(self):
        result = self.full()
        self.assertEqual(result['applications'][0]['healthy_app_replicas'], 2)
        self.assertEqual(result['applications'][0]['ha_status'], 'REPLIQUES_PRETES_HTTPS_NON_VALIDE')

    def test_one_replica_not_ha(self):
        states = {(NODE_A, ENV, RUN): 'HEALTHY', (NODE_B, ENV, RUN): 'UNHEALTHY'}
        data = module.assemble(MIG, {'applications': [DECISION], 'cloud_https_ready': False},
                               {NODE_A: [ENTRY], NODE_B: [ENTRY]}, states)
        self.assertEqual(data['applications'][0]['healthy_app_replicas'], 1)
        self.assertEqual(data['applications'][0]['ha_status'], 'NON_VALIDEE')

    def test_no_report_no_false_ha(self):
        data = module.assemble(MIG, None, {NODE_A: [ENTRY], NODE_B: [ENTRY]},
                               {(NODE_A, ENV, RUN): 'HEALTHY', (NODE_B, ENV, RUN): 'HEALTHY'})
        self.assertEqual(data['applications'][0]['ha_status'], 'INCONNUE')

    def test_empty_report_and_manifests(self):
        data = module.assemble(MIG, {'applications': [], 'cloud_https_ready': False},
                               {NODE_A: [], NODE_B: []})
        self.assertEqual(data['applications'], [])
        self.assertEqual(len(data['workers']), 2)

    def test_gcs_failure_is_not_empty_manifest(self):
        def unavailable(_):
            raise OSError('no access')
        entries, error = module._fetch_desired(NODE_A, unavailable)
        self.assertIsNone(entries)
        self.assertIn('impossible', error)

    def test_wrong_worker_manifest_is_rejected(self):
        rows, error = module._fetch_desired(NODE_A, lambda _: {'instance': NODE_B, 'applications': [ENTRY]})
        self.assertIsNone(rows)
        self.assertIn('invalide', error)

    def test_ha_filter_json(self):
        data = self.full()
        data['errors'] = []
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            module.output(data, ha_only=True, as_json=True)
        self.assertIn('ladyportfolio.auto.ikom.mg', buf.getvalue())
        self.assertNotIn('"ha_status": "ACTIVE"', buf.getvalue())

    def test_health_accepts_canonical_google_selflink(self):
        base = 'https://compute.googleapis.com/compute/v1/projects/plucky-stacker-480421-c4'
        tag = ENV.replace('-', '')[:12] + '-' + RUN.replace('-', '')[:12]
        group = ('https://www.googleapis.com/compute/v1/projects/plucky-stacker-480421-c4'
                 f'/zones/us-central1-a/networkEndpointGroups/ak-n-{tag}')
        calls = []
        def compute(method, route, payload=None, missing_ok=False):
            calls.append((method, route, payload))
            if method == 'GET':
                return {'backends': [{'group': group}]}
            return {'healthStatus': [{'instance': 'projects/x/zones/us-central1-a/instances/' + NODE_A,
                                      'healthState': 'HEALTHY'}]}
        result = module._health_for_app({'environment': ENV, 'run_id': RUN}, MIG[0], compute, base)
        self.assertEqual(result, 'HEALTHY')
        self.assertEqual(calls[-1][2]['group'], group)

    def test_positive_status_requires_external_verification(self):
        doc = {'applications': [dict(DECISION, status='HTTPS_READY')],
               'cloud_https_ready': True, 'public_routing_verified': True}
        result = self.full(report=doc)
        self.assertEqual(result['applications'][0]['ha_status'], 'ACTIVE')

    def test_https_flag_alone_not_enough(self):
        doc = {'applications': [dict(DECISION, status='HTTPS_READY')], 'cloud_https_ready': True}
        self.assertEqual(self.full(report=doc)['applications'][0]['ha_status'],
                         'REPLIQUES_PRETES_HTTPS_NON_VALIDE')


if __name__ == '__main__':
    unittest.main()

class HaAppsWideAndDiagnosticsTests(unittest.TestCase):
    def test_table_wide_with_empty_workers(self):
        data = module.assemble(MIG, {'applications': [], 'cloud_https_ready': False},
                               {NODE_A: [], NODE_B: []})
        data['errors'] = []
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            module.output(data)
        output = buf.getvalue()
        self.assertIn('WORKER', output)
        self.assertIn('IP_PRIVEE', output)
        self.assertIn('10.128.0.13', output)
        self.assertIn('AUCUNE', output)
        self.assertIn('0 application(s) GCP', output)

    def test_table_with_app_reports_real_replica_health_and_ha_status(self):
        data = module.assemble(MIG, {'applications': [DECISION], 'cloud_https_ready': False},
                               {NODE_A: [ENTRY], NODE_B: [ENTRY]},
                               {(NODE_A, ENV, RUN): 'HEALTHY',
                                (NODE_B, ENV, RUN): 'UNHEALTHY'})
        data['errors'] = []
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            module.output(data)
        table = buf.getvalue()
        self.assertIn('ladyportfolio.auto.ikom.mg', table)
        self.assertIn('1/2', table)
        self.assertIn('NON_VALIDEE', table)

    def test_report_filter_identifies_broker_inputs_not_delivery_eligible(self):
        doc = {'run_id': RUN, 'cloud_delivery_requested': True,
               'internal_port': 8080, 'healthcheck': '/', 'memory_mb': 3300,
               'cpu_millicores': 2000, 'variables_present': False}
        errors = module._delivery_blocks(doc, {'run_id': RUN,
            'image': 'us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images/x@sha256:'+'a'*64},
            hosts=['ladyportfolio.auto.ikom.mg'])
        self.assertIn('RAM_HORS_BORNE_DELIVERY', errors)
        self.assertIn('CPU_HORS_BORNE_DELIVERY', errors)
        self.assertNotIn('IMAGE_REGISTRY_ABSENTE', errors)

    def test_no_unproven_ha_during_diagnose(self):
        data = module.assemble(MIG, {'applications': [], 'cloud_https_ready': False},
                               {NODE_A: [], NODE_B: []})
        data['errors'] = []
        data['local_releases'] = [{'environment': ENV, 'run_id': RUN,
            'host': 'ladyportfolio.auto.ikom.mg', 'ha_requested': True,
            'cloud_delivery_requested': True, 'delivery_eligible': False,
            'assigned': False, 'status': 'EXCLU', 'reasons': ['RAM_HORS_BORNE_DELIVERY']}]
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            module.output(data, diagnose=True)
        result = buf.getvalue()
        self.assertIn('HORS MIG', result)
        self.assertIn('RAM_HORS_BORNE_DELIVERY', result)
        self.assertIn('0 HA publique(s) validée(s)', result)

    def test_ineligible_if_domain_or_published_image_absent(self):
        errors = module._delivery_blocks({'run_id': RUN, 'cloud_delivery_requested': True,
            'memory_mb': 512, 'cpu_millicores': 500, 'internal_port': 8080,
            'healthcheck': '/'}, None)
        self.assertIn('IMAGE_REGISTRY_ABSENTE', errors)
        self.assertIn('DOMAINE_NON_ASSOCIE', errors)

    def test_json_diagnose_no_private_image_or_env_values(self):
        data = module.assemble(MIG, {'applications': [], 'cloud_https_ready': False},
                               {NODE_A: [], NODE_B: []})
        data['errors'] = []
        data['local_releases'] = [{'environment': ENV, 'run_id': RUN,
            'host': 'ladyportfolio.auto.ikom.mg', 'ha_requested': True,
            'cloud_delivery_requested': True, 'delivery_eligible': False,
            'assigned': False, 'status': 'EXCLU', 'reasons': ['STACK_NON_ATOMIQUE']}]
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            module.output(data, as_json=True, diagnose=True)
        obj = __import__('json').loads(buf.getvalue())
        self.assertEqual(obj['local_releases'][0]['status'], 'EXCLU')
        self.assertEqual(obj['applications'], [])
