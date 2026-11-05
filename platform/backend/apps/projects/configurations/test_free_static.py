"""Enforce free/static policy on Django even if a client crafts API requests."""
from types import SimpleNamespace
from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied
from apps.projects.configurations.services import check_entitlement


class FreeStaticEntitlementTests(SimpleTestCase):
    def setUp(self):
        plan = SimpleNamespace(is_premium=False, cpu_millicores=250,
                               memory_mb=512, storage_mb=1024, max_instances=1)
        offer = SimpleNamespace(code='DEVOPS')
        self.entitlement = SimpleNamespace(resource_plan=plan, mode_offer=offer,
                                           status='ACTIVE', expires_at=None)
    def cfg(self, kind):
        return SimpleNamespace(environment=SimpleNamespace(entitlement=self.entitlement),
                               mode='DEVOPS', build_method=kind, resource_plan=None)
    def test_free_vite_allowed(self):
        self.assertIs(check_entitlement(self.cfg('STATIC')), self.entitlement.resource_plan)
    def test_free_cannot_request_server_dockerfile(self):
        with self.assertRaises(PermissionDenied):
            check_entitlement(self.cfg('DOCKERFILE'))
