import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts/ha'), str(ROOT / 'scripts/lib')]
import autokom_ha_delivery as delivery
import autokom_ha_dns as dns

RETIRED = '44e76357-2375-47c2-b860-77bb31c5e202'
LIVE = 'bb40a558-adb5-4546-a17d-50e6510ed906'
HOST = 'portfoliolady.auto.ikom.mg'
WORKER = {'name': 'autokom-ha-web-wx10', 'zone': 'us-central1-a'}
DESIRED = {'schema': 1, 'instance': WORKER['name'], 'zone': WORKER['zone'],
           'applications': [{'environment_id': RETIRED}, {'environment_id': LIVE}]}


class CleanupTests(unittest.TestCase):
    def test_recovers_retired_app_from_manifests_after_report_lost_it(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(delivery, 'STATE', Path(folder)):
            with patch.object(delivery, 'gcs_get', return_value=DESIRED):
                manifests = delivery.previous_manifests([WORKER])
            records = {item['environment_id'] for doc in manifests.values() for item in doc['applications']}
            self.assertEqual(delivery.deleted_environments({}, [{'environment_id': LIVE}], recorded=records), [RETIRED])
            local = Path(folder) / RETIRED
            local.mkdir()
            (local / 'current.json').touch()
            self.assertEqual(delivery.deleted_environments({}, [], recorded=[RETIRED]), [])

    def test_unroutes_before_writing_manifests_and_preserves_other_apps(self):
        steps = []
        with patch.object(delivery, 'locally_retired', return_value=True), \
                patch.object(delivery, 'unroute_deleted', side_effect=lambda ids: steps.append(('route', ids))), \
                patch.object(delivery, 'gcs_put', side_effect=lambda path, doc: steps.append(('manifest', doc))), \
                patch.object(dns, 'clear_retired_marker', side_effect=lambda host, env: steps.append(('marker', host)) or True):
            delivery.withdraw_deleted([RETIRED], {WORKER['name']: DESIRED}, [{'host': HOST, 'environment': RETIRED}])
        self.assertEqual(steps, [('route', {RETIRED}), ('manifest', {**DESIRED, 'applications': [{'environment_id': LIVE}]}),
                                 ('marker', HOST)])

    def test_failed_urlmap_write_never_removes_desired_replicas(self):
        with patch.object(delivery, 'locally_retired', return_value=True), \
                patch.object(delivery, 'unroute_deleted', side_effect=delivery.DeliveryError('failure')), \
                patch.object(delivery, 'gcs_put') as put:
            with self.assertRaises(delivery.DeliveryError):
                delivery.withdraw_deleted([RETIRED], {WORKER['name']: DESIRED}, [])
            put.assert_not_called()

    def test_retired_marker_is_not_a_cloudflare_mutation(self):
        broker = dns._broker()
        with tempfile.TemporaryDirectory() as folder, patch.object(broker, 'STATE', Path(folder)), \
                patch.object(broker, '_runtime_lock_call', side_effect=lambda fn, _: fn(None)), \
                patch.object(dns, 'marker', return_value={'environment': RETIRED}), \
                patch.object(dns, 'ownership', return_value=None) as ownership, \
                patch.object(dns, '_request') as request, patch.object(dns, 'forget') as forget:
            self.assertTrue(dns.clear_retired_marker(HOST, RETIRED))
            forget.assert_called_once_with(HOST)
            ownership.return_value = RETIRED
            self.assertFalse(dns.clear_retired_marker(HOST, RETIRED))
            request.assert_not_called()

    def test_retirement_runs_before_another_domain_dns_wait(self):
        cloud_host = 'cv-admin-dashboard-lady.auto.ikom.mg'
        marker = {'host': cloud_host, 'environment': LIVE, 'run_id': LIVE, 'workers': []}
        decision = {'environment': LIVE, 'ha': True, 'run_id': LIVE, 'status': 'ASSIGNED',
                    'workers': [WORKER], 'hosts': [cloud_host]}
        actions = []
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for obj, name, args in [
                (delivery, 'STATE', {'new': Path(folder)}),
                (delivery, 'committed_apps', {'return_value': [{'environment_id': LIVE}]}),
                (delivery, 'running_workers', {'return_value': [WORKER]}),
                (delivery, 'autoscaler_status', {'return_value': (2, 5)}),
                (delivery, 'desired_floor', {'return_value': {'target': 2}}),
                (delivery, 'assign', {'return_value': ({WORKER['name']: [{'environment_id': LIVE}]}, [decision])}),
                (delivery, '_private_json', {'return_value': {'applications': []}}),
                (delivery, 'gcs_get', {'return_value': DESIRED}),
                (delivery, '_atomic_report', {'side_effect': lambda doc: actions.append(('report', list(doc.get('pending_deletions', []))))}),
                (delivery, '_write_public_metrics', {}),
                (delivery, 'unroute_deleted', {'side_effect': lambda _: actions.append(('route', None))}),
                (delivery, 'gcs_put', {'side_effect': lambda _, doc: actions.append(('manifest', doc['applications']))}),
                (dns, 'tracked', {'return_value': [marker]}),
                (dns, 'transition', {'return_value': 'PRIMARY_PROPAGATING'}),
            ]:
                stack.enter_context(patch.object(obj, name, **args))
            report = delivery.reconcile(public_dns=True)
        self.assertEqual(actions[:3], [('report', [RETIRED]), ('route', None),
                                       ('manifest', [{'environment_id': LIVE}])])
        self.assertEqual(report['dns_fallback'], {cloud_host: 'PRIMARY_PROPAGATING'})
        self.assertNotIn('pending_deletions', report)


if __name__ == '__main__':
    unittest.main()
