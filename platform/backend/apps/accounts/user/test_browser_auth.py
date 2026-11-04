"""Tests de non-régression du nouveau mode navigateur, indépendamment de Bearer."""
from django.test import TestCase, Client, override_settings
from apps.accounts.user.models import User, UserSession

PASSWORD = 'Browser-Secure-Strong-Password-2026!'


@override_settings(CLIENT_BROWSER_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
                   CSRF_TRUSTED_ORIGINS=['https://auto.ik0m.tech'])
class BrowserCookieAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='cookie@autokom.test', full_name='Cookie Example',
                                        is_email_verified=True, is_active=True)
        self.user.set_password(PASSWORD)
        self.user.save(update_fields=['password'])
        self.browser = Client(enforce_csrf_checks=True)

    def csrf(self):
        response = self.browser.get('/api/auth/browser/csrf/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrftoken', self.browser.cookies)
        return self.browser.cookies['csrftoken'].value

    def login(self):
        token = self.csrf()
        return self.browser.post('/api/auth/browser/login/',
                                 data={'email': self.user.email, 'password': PASSWORD},
                                 content_type='application/json', secure=True,
                                 HTTP_ORIGIN='https://auto.ik0m.tech', HTTP_X_CSRFTOKEN=token)

    def test_login_requires_csrf_even_for_anonymous(self):
        response = self.browser.post('/api/auth/browser/login/',
                                     data={'email': self.user.email, 'password': PASSWORD},
                                     content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UserSession.objects.count(), 0)

    def test_cookie_attributes_no_jwt_json_and_same_origin_me(self):
        response = self.login()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('tokens', response.json()['data'])
        for name, path in [('autokom_access', '/api/'), ('autokom_refresh', '/api/auth/browser/')]:
            cookie = response.cookies[name]
            self.assertTrue(cookie['httponly'])
            self.assertTrue(cookie['secure'])
            self.assertEqual(cookie['samesite'], 'Lax')
            self.assertEqual(cookie['path'], path)
            self.assertFalse(cookie['domain'])
        self.assertEqual(self.browser.get('/api/auth/me/', secure=True).status_code, 200)
        self.assertEqual(self.browser.post('/api/auth/logout-all/', data={}, content_type='application/json', secure=True).status_code, 403)

    def test_refresh_rotates_cookies_and_logout_revokes_session(self):
        self.login()
        before = self.browser.cookies['autokom_refresh'].value
        token = self.browser.cookies['csrftoken'].value
        refreshed = self.browser.post('/api/auth/browser/refresh/', data={}, content_type='application/json',
                                      HTTP_ORIGIN='https://auto.ik0m.tech', HTTP_X_CSRFTOKEN=token, secure=True)
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        self.assertNotEqual(before, self.browser.cookies['autokom_refresh'].value)
        self.assertNotIn('tokens', refreshed.json()['data'])
        logged_out = self.browser.post('/api/auth/browser/logout/', data={}, content_type='application/json',
                                      HTTP_ORIGIN='https://auto.ik0m.tech', HTTP_X_CSRFTOKEN=token, secure=True)
        self.assertEqual(logged_out.status_code, 200, logged_out.content)
        self.assertEqual(logged_out.cookies['autokom_access'].value, '')
        self.assertEqual(logged_out.cookies['autokom_refresh'].value, '')
        self.assertEqual(self.browser.get('/api/auth/me/', secure=True).status_code, 401)

    def test_legacy_bearer_is_unchanged(self):
        response = self.browser.post('/api/auth/login/',
                                     data={'email': self.user.email, 'password': PASSWORD},
                                     content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json()['data']['tokens'])
        self.assertNotIn('autokom_access', response.cookies)
