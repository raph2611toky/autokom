"""Tests Django à exécuter sur le serveur de test, sans accès GitHub réel."""
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory
from rest_framework.exceptions import ValidationError
from apps.projects.git.views import GitHubCallbackAPIView
from apps.accounts.user.github_views import GitHubLoginCallbackAPIView


@override_settings(FRONTEND_URL='https://auto.ikom.mg')
class GitHubCallbackSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch('apps.projects.git.views.log.warning')
    @patch('apps.projects.git.views.exchange_code', side_effect=ValidationError('Échec OAuth'))
    @patch('apps.projects.git.views.consume_state', return_value=object())
    def test_repo_error_stage_is_logged_without_code_or_state(self, consume, exchange, warning):
        response = GitHubCallbackAPIView.as_view()(self.factory.get('/api/projects/git/github/callback/',
            {'code': 'private-oauth-code-123', 'state': 'private-oauth-state-123'}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('status=error', response['Location'])
        self.assertNotIn('private-oauth', response['Location'])
        text = repr(warning.call_args)
        self.assertIn('exchange', text)
        self.assertNotIn('private-oauth-code', text)
        self.assertNotIn('private-oauth-state', text)

    @patch('apps.accounts.user.github_views.log.warning')
    @patch('apps.accounts.user.github_views.verified_identity', side_effect=ValidationError('Échec OAuth'))
    @patch('apps.accounts.user.github_views.consume_oauth_state', return_value=('login', 'verifier', None, 'hash'))
    def test_account_error_does_not_publish_oauth_code(self, consume, identity, warning):
        response = GitHubLoginCallbackAPIView.as_view()(self.factory.get('/api/auth/github/callback/',
            {'code': 'private-oauth-code-123', 'state': 'private-oauth-state-123'}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('#status=error', response['Location'])
        self.assertNotIn('private-oauth', response['Location'])
        self.assertIn('identity', repr(warning.call_args))
        self.assertNotIn('private-oauth-code', repr(warning.call_args))
