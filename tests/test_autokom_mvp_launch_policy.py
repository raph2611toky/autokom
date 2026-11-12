"""Pure policy regression suite; no database or payment provider required."""
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

POLICY = (Path(__file__).resolve().parents[1] / 'platform/backend/apps/plans/entitlements/mvp_policy.py')
spec = importlib.util.spec_from_file_location('autokom_mvp_policy', POLICY)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
UTC = dt.timezone.utc
START = dt.datetime(2026, 11, 12, 8, 0, tzinfo=UTC)


class MVPOfferTests(unittest.TestCase):
    def assert_duration(self, backend, ha, days):
        result = policy.entitlement_window(START, has_backend=backend,
                                            high_availability=ha,
                                            now=START + dt.timedelta(days=days-1))
        self.assertEqual(result['days'], days)
        self.assertEqual(result['expires_at'], START + dt.timedelta(days=days))
        self.assertEqual(result['state'], 'active')

    def test_frontend_30_days(self):
        self.assert_duration(False, False, 30)

    def test_frontend_ha_15_days(self):
        self.assert_duration(False, True, 15)

    def test_stack_15_days(self):
        self.assert_duration(True, False, 15)

    def test_stack_ha_7_days(self):
        self.assert_duration(True, True, 7)

    def test_backend_added_keeps_original_start(self):
        before = policy.entitlement_window(START, now=START + dt.timedelta(days=2))
        after = policy.entitlement_window(START, has_backend=True,
                                           now=START + dt.timedelta(days=2))
        self.assertEqual(before['expires_at'], START + dt.timedelta(days=30))
        self.assertEqual(after['expires_at'], START + dt.timedelta(days=15))

    def test_grace_begins_exactly_at_expiry(self):
        at = START + dt.timedelta(days=30)
        result = policy.entitlement_window(START, now=at)
        self.assertEqual(result['state'], 'grace')
        self.assertEqual(result['remaining_seconds'], 0)
        self.assertEqual(result['purge_after'], at + dt.timedelta(hours=24))

    def test_purge_review_only_after_24_hours(self):
        at = START + dt.timedelta(days=31)
        self.assertEqual(policy.entitlement_window(START, now=at)['state'], 'purge_due')

    def test_unlimited_admin_has_no_expiry(self):
        result = policy.entitlement_window(START, has_backend=True,
                                            high_availability=True, unlimited=True,
                                            now=START + dt.timedelta(days=1000))
        self.assertEqual(result['state'], 'unlimited')
        self.assertIsNone(result['expires_at'])

    def test_created_unpublished_project_not_aged(self):
        result = policy.entitlement_window(None, now=START)
        self.assertEqual(result['state'], 'not_started')
        self.assertIsNone(result['expires_at'])

    def test_all_durations_reachable(self):
        self.assertEqual(sorted(set(policy.DAYS.values())), [7,15,30])


if __name__ == '__main__':
    unittest.main()
