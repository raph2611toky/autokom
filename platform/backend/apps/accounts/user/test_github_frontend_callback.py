"""Sécurité: state lié au navigateur, CSRF et cookies HttpOnly sans JWT JSON."""
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from apps.accounts.user.models import User
from apps.accounts.user.github_auth_service import start_oauth, consume_oauth_state
from rest_framework.exceptions import ValidationError
from urllib.parse import urlsplit, parse_qs


@override_settings(GITHUB_APP_CLIENT_ID='Iv1.test', GITHUB_APP_CLIENT_SECRET='not-real',
    GITHUB_APP_CALLBACK_URL='https://backend.example.invalid/api/projects/git/github/callback/',
    GITHUB_AUTH_CALLBACK_URL='', FRONTEND_URL='https://auto.example.invalid',
    CSRF_TRUSTED_ORIGINS=['https://auto.example.invalid'],
    TOKEN_ENCRYPTION_KEY='encryption-test-very-long-stable-secret-key-123456789',
    CLIENT_BROWSER_COOKIE_SECURE=True)
class GitHubFrontendCallbackTests(TestCase):
    def setUp(self):
        self.browser = Client(enforce_csrf_checks=True)
        self.url = '/api/auth/browser/github/complete/'

    def _oauth(self, intent='signup'):
        url, binding = start_oauth(intent)
        state = parse_qs(urlsplit(url).query)['state'][0]
        return state, binding

    def _send(self, state, binding, code='g' * 40, csrf=True):
        headers = {'HTTP_ORIGIN': 'https://auto.example.invalid'}
        if csrf:
            self.browser.get('/api/auth/browser/csrf/', secure=True)
            headers['HTTP_X_CSRFTOKEN'] = self.browser.cookies['csrftoken'].value
        return self.browser.post(self.url, data={'state': state, 'browser_binding': binding, 'code': code},
            content_type='application/json', secure=True, **headers)

    def test_csrf_required_for_oauth_code_exchange(self):
        state, binding = self._oauth()
        response = self._send(state, binding, csrf=False)
        self.assertEqual(response.status_code, 403)

    def test_wrong_browser_binding_does_not_consume_state(self):
        state, binding = self._oauth()
        response = self._send(state, 'z' * 50)
        self.assertEqual(response.status_code, 400)
        with patch('apps.accounts.user.browser_views.verified_identity', return_value=(1234,'testlogin','new@example.invalid','New')):
            correct = self._send(state, binding)
        self.assertEqual(correct.status_code, 200, correct.content)
        self.assertEqual(correct.json()['data']['status'], 'created')
        self.assertIn('autokom_access', correct.cookies)
        self.assertTrue(correct.cookies['autokom_access']['httponly'])
        self.assertNotIn('tokens', correct.json()['data'])
        self.assertNotIn('code', correct.json()['data'])
        self.assertNotIn('access_token', str(correct.json()))
        self.assertEqual(self.browser.get('/api/auth/me/', secure=True).status_code, 200)
        replay = self._send(state, binding)
        self.assertEqual(replay.status_code, 400)

    def test_linked_existing_github_user_signs_in_without_duplicate(self):
        state, binding = self._oauth()
        fake = (5678,'existing-gh','verified@example.invalid','Tester')
        with patch('apps.accounts.user.browser_views.verified_identity', return_value=fake):
            created = self._send(state, binding)
        self.assertEqual(created.json()['data']['status'], 'created')
        state, binding = self._oauth('login')
        with patch('apps.accounts.user.browser_views.verified_identity', return_value=fake):
            login = self._send(state, binding)
        self.assertEqual(login.json()['data']['status'], 'ok')
        self.assertEqual(User.objects.filter(email='verified@example.invalid').count(), 1)
