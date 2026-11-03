"""Runtime integration checks, requiring a MySQL test database."""
import json
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.user.models import User


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DocumentationAndUploadTests(TestCase):
    def test_swagger_requires_native_administrator_session(self):
        browser = Client()
        reply = browser.get('/api/docs/')
        self.assertEqual(reply.status_code, 302)
        self.assertIn('/docs/login/', reply.url)
        admin = get_user_model().objects.create_superuser(
            email='docs-admin@autokom.test', password='A-Strong-Admin-Password-2026')
        self.assertTrue(browser.login(username=admin.email, password='A-Strong-Admin-Password-2026'))
        reply = browser.get('/api/docs/')
        self.assertEqual(reply.status_code, 200)

    def test_swagger_has_multipart_form_without_request_body(self):
        admin = get_user_model().objects.create_superuser(
            email='schema-admin@autokom.test', password='A-Strong-Admin-Password-2026')
        browser = Client()
        browser.force_login(admin)
        reply = browser.get('/api/schema.json', HTTP_ACCEPT='application/json')
        self.assertEqual(reply.status_code, 200)
        schema = json.loads(reply.content)
        avatar_path = next((v for k,v in schema['paths'].items() if k.endswith('/me/avatar/')), None)
        self.assertIsNotNone(avatar_path)
        op = avatar_path['patch']
        parameters = op.get('parameters', [])
        self.assertTrue(any(p['in'] == 'formData' and p['name'] == 'avatar' for p in parameters))
        self.assertFalse(any(p['in'] == 'body' for p in parameters))
        self.assertEqual(op['responses']['200']['schema']['properties']['data']['properties']['user']['type'], 'object')
