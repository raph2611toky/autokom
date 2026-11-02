"""Contrôles sans accès à MySQL ; lancer avec manage.py test tests.test_platform_models."""
from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase


class PlatformModelTests(SimpleTestCase):
    def test_17_apps_and_27_models(self):
        self.assertEqual(len(settings.LOCAL_APPS), 17)
        models = [m for a in apps.get_app_configs() if a.name.startswith('apps.') for m in a.get_models()]
        self.assertEqual(len(models), 27)
        self.assertEqual(len({m._meta.db_table for m in models}), 27)
        for model in models:
            self.assertIn('__str__', model.__dict__, model.__name__)

    def test_mysql_only_and_no_infrastructure_app(self):
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.mysql')
        self.assertFalse(any(x.startswith('apps.infrastructure.') for x in settings.LOCAL_APPS))

    def test_default_devops_and_offers_independent(self):
        from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
        from apps.plans.entitlements.models import ApplicationEntitlement
        self.assertEqual(DeploymentModeOffer.Mode.DEVOPS, 'DEVOPS')
        self.assertTrue(hasattr(ResourcePlan, 'cpu_millicores'))
        self.assertIsNotNone(ApplicationEntitlement._meta.get_field('resource_plan'))
        self.assertIsNotNone(ApplicationEntitlement._meta.get_field('mode_offer'))

    def test_only_one_method_per_client_api_view(self):
        from rest_framework.views import APIView
        from apps.accounts.user import views
        from inspect import isclass
        for name in dir(views):
            candidate = getattr(views, name)
            if not isclass(candidate) or not name.endswith('APIView') or candidate is APIView:
                continue
            self.assertEqual(candidate.__bases__, (APIView,))
            methods = [x for x in ('get','post','put','patch','delete') if x in candidate.__dict__]
            self.assertEqual(len(methods), 1, name)
