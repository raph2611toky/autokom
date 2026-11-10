"""Integration against the actual Django entitlement gate; run with manage.py test."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied
from apps.projects.configurations.services import check_entitlement
from apps.plans.entitlements.models import HighAvailabilityOption


class EntitlementHAExplicitOptInTests(SimpleTestCase):
    def cfg(self, requested=False, granted_instances=1, chosen_instances=None):
        granted = SimpleNamespace(code='starter', is_premium=granted_instances > 1,
            cpu_millicores=250, memory_mb=256, storage_mb=1024, max_instances=granted_instances)
        chosen = (None if chosen_instances is None else SimpleNamespace(
            cpu_millicores=250, memory_mb=256, storage_mb=1024, max_instances=chosen_instances))
        entitlement = SimpleNamespace(resource_plan=granted, mode_offer=SimpleNamespace(code='DEVOPS'),
            status='ACTIVE', expires_at=None)
        return SimpleNamespace(environment=SimpleNamespace(entitlement=entitlement), mode='DEVOPS',
            build_method='STATIC', high_availability_requested=requested, resource_plan=chosen)

    def test_professional_ui_must_not_force_ha_without_user_opt_in(self):
        cfg = self.cfg(requested=False)
        self.assertIs(check_entitlement(cfg), cfg.environment.entitlement.resource_plan)

    def test_explicit_ha_blocked_when_actual_grant_is_one_instance(self):
        with patch.object(HighAvailabilityOption.objects, 'filter') as query:
            query.return_value.first.return_value = None
            with self.assertRaisesRegex(PermissionDenied, 'plan réellement attribué'):
                check_entitlement(self.cfg(requested=True, granted_instances=1))

    def test_entitled_two_instances_and_active_option_allows_ha(self):
        option = SimpleNamespace(status=HighAvailabilityOption.Status.ACTIVE, expires_at=None)
        with patch.object(HighAvailabilityOption.objects, 'filter') as query:
            query.return_value.first.return_value = option
            cfg = self.cfg(requested=True, granted_instances=2)
            self.assertIs(check_entitlement(cfg), cfg.environment.entitlement.resource_plan)

    def test_entitled_plan_still_requires_active_ha_option(self):
        with patch.object(HighAvailabilityOption.objects, 'filter') as query:
            query.return_value.first.return_value = None
            with self.assertRaisesRegex(PermissionDenied, 'option non active'):
                check_entitlement(self.cfg(requested=True, granted_instances=2))

    def test_lower_selected_profile_cannot_consume_two_instances(self):
        option = SimpleNamespace(status=HighAvailabilityOption.Status.ACTIVE, expires_at=None)
        with patch.object(HighAvailabilityOption.objects, 'filter') as query:
            query.return_value.first.return_value = option
            with self.assertRaisesRegex(PermissionDenied, 'profil de ressources sélectionné'):
                check_entitlement(self.cfg(requested=True, granted_instances=2, chosen_instances=1))
