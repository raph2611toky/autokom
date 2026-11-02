from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase, RequestFactory
from django.urls import resolve
from config.views import handle_404


class PlatformStructureTests(SimpleTestCase):
    def test_mysql_is_mandatory(self):
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.mysql')

    def test_all_business_models_implement_str(self):
        expected = [
            'administrators', 'accounts', 'organizations', 'integrations', 'projects',
            'environments', 'configurations', 'secrets', 'deployments', 'pipelines',
            'releases', 'sites', 'domains', 'infrastructure', 'resources', 'policies',
            'operations', 'monitoring', 'assistant', 'notifications', 'auditing',
        ]
        models = [model for label in expected for model in apps.get_app_config(label).get_models()]
        self.assertEqual(len(models), 67)
        for model in models:
            self.assertIn('__str__', model.__dict__, model.__name__)
            self.assertNotIn('TimestampedUUIDModel', str(model.__mro__))

    def test_production_api_404_contract(self):
        request = RequestFactory().get('/api/introuvable/')
        response = handle_404(request, Exception('missing'))
        self.assertEqual(response.status_code, 404)
        import json
        self.assertEqual(json.loads(response.content), {
            'success': False, 'message': 'Route API introuvable.',
            'data': None, 'errors': None,
        })

    def test_swagger_login_page_is_a_view(self):
        match = resolve('/docs/login/')
        self.assertTrue(match.func.view_class.__name__.endswith('APIView'))

    def test_client_account_and_django_admin_models_are_distinct(self):
        from django.contrib.auth import get_user_model
        from apps.accounts.user.models import User
        self.assertNotEqual(get_user_model(), User)
        self.assertEqual(get_user_model().__name__, 'Administrator')
