"""Regression cases for the isolated client and administrator authentication systems."""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase
from helpers.security import token_digest
from apps.accounts.user.models import User, UserActionToken, UserSession

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountsLifecycleTests(APITestCase):
    def register(self):
        response = self.client.post('/api/auth/register/', {
            'email': 'client@autokom.test', 'full_name': 'Client Test',
            'password': 'ThisIs-A-Unique-Strong-Passphrase-2026',
            'password_confirm': 'ThisIs-A-Unique-Strong-Passphrase-2026',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return User.objects.get(email='client@autokom.test')

    def login(self):
        return self.client.post('/api/auth/login/', {
            'email': 'client@autokom.test', 'password': 'ThisIs-A-Unique-Strong-Passphrase-2026',
        }, format='json')

    def test_admin_and_client_accounts_are_distinct(self):
        self.assertNotEqual(get_user_model(), User)
        admin = get_user_model().objects.create_superuser(
            email='admin@autokom.test', password='TestAdmin-Passphrase-2026')
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertFalse(User.objects.filter(email=admin.email).exists())
        client = self.register()
        self.assertFalse(get_user_model().objects.filter(email=client.email).exists())
        self.assertEqual(client.memberships.count(), 1)
        self.assertEqual(client.memberships.first().role, 'OWNER')

    def test_verification_login_refresh_logout_and_replay(self):
        user = self.register()
        response = self.login()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(user.is_email_verified)
        # Simulate consuming the secret from the verification email.
        from django.core import mail
        from urllib.parse import urlsplit, parse_qs
        # Email is HTML; extract the URL from href to avoid depending on the email body format.
        import re, html
        email_html = mail.outbox[-1].alternatives[0][0]
        link = html.unescape(re.search(r'href="([^"]+)"', email_html).group(1))
        token = parse_qs(urlsplit(link).query)['token'][0]
        response = self.client.post('/api/auth/email/verify/', {'token': token}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.login()
        self.assertEqual(response.status_code, 200, response.data)
        first_access = response.data['data']['tokens']['access']
        refresh = response.data['data']['tokens']['refresh']
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + first_access)
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
        self.client.credentials()
        rotated = self.client.post('/api/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertNotEqual(rotated.data['data']['tokens']['refresh'], refresh)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + first_access)
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        self.client.credentials()
        reused = self.client.post('/api/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(reused.status_code, 401)
        self.assertEqual(UserSession.objects.filter(user=user, revoked_at__isnull=True).count(), 0)

    def test_password_reset_revokes_sessions(self):
        user = self.register()
        user.is_email_verified = True
        user.save(update_fields=['is_email_verified'])
        login = self.login()
        self.assertEqual(login.status_code, 200, login.data)
        access = login.data['data']['tokens']['access']
        from django.core import mail
        self.client.post('/api/auth/password/forgot/', {'email': user.email}, format='json')
        import re, html
        from urllib.parse import urlsplit, parse_qs
        email_html = mail.outbox[-1].alternatives[0][0]
        link = html.unescape(re.search(r'href="([^"]+)"', email_html).group(1))
        token = parse_qs(urlsplit(link).query)['token'][0]
        response = self.client.post('/api/auth/password/reset/', {
            'token': token, 'new_password': 'Another-Strong-Passphrase-2026',
            'new_password_confirm': 'Another-Strong-Passphrase-2026',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + access)
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.login().status_code, 401)
        self.assertTrue(User.objects.get(pk=user.pk).check_password('Another-Strong-Passphrase-2026'))

    def test_non_enumerating_recovery(self):
        response = self.client.post('/api/auth/password/forgot/', {'email': 'unknown@autokom.test'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])

    def test_public_registration_cannot_create_admin(self):
        response = self.client.post('/api/auth/register/', {
            'email': 'malicious@autokom.test', 'full_name': 'Abc',
            'password': 'ThisIs-A-Unique-Strong-Passphrase-2026',
            'password_confirm': 'ThisIs-A-Unique-Strong-Passphrase-2026',
            'is_staff': True, 'is_superuser': True, 'role': 'ADMIN',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertFalse(get_user_model().objects.filter(email='malicious@autokom.test').exists())
        self.assertFalse(hasattr(User.objects.get(email='malicious@autokom.test'), 'is_staff'))
