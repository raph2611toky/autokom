"""Tests ciblés. Exécution : python manage.py test apps.accounts.user.test_github_auth"""
import re
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings
from helpers.secure_actions import generate_secure_action_token, validate_secure_action_token, SecureActionTokenError
from apps.accounts.user.github_auth_service import auth_callback_url, start_oauth


@override_settings(
    SECRET_KEY='django-tests-very-long-stable-secret-key-123456789',
    TOKEN_ENCRYPTION_KEY='encryption-test-very-long-stable-secret-key-123456789',
    CLIENT_JWT_SIGNING_KEY='client-signing-test-very-long-stable-secret-key-12345',
    CLIENT_JWT_ISSUER='autokom-test',
    GITHUB_APP_CLIENT_ID='Iv1.test',
    GITHUB_APP_CLIENT_SECRET='testing-only-not-a-real-secret',
    GITHUB_APP_CALLBACK_URL='https://backend.example.invalid/api/projects/git/github/callback/',
    GITHUB_AUTH_CALLBACK_URL='',
    FRONTEND_URL='https://auto.example.invalid',
)
class GitHubSecureAuthTests(SimpleTestCase):
    def test_action_token_signed_encrypted_versioned_and_not_plaintext(self):
        user = SimpleNamespace(pk=uuid.uuid4(), token_version=4)
        token = generate_secure_action_token(user, purpose='RESET_PASSWORD', lifetime=timedelta(minutes=10))
        self.assertTrue(token.startswith('ar1.'))
        self.assertNotIn(str(user.pk), token)
        self.assertNotIn('RESET_PASSWORD', token)
        claims = validate_secure_action_token(token, user=user, expected_purpose='RESET_PASSWORD')
        self.assertEqual(claims['sub'], str(user.pk))
        with self.assertRaises(SecureActionTokenError):
            validate_secure_action_token(token, user=user, expected_purpose='VERIFY_EMAIL')
        with self.assertRaises(SecureActionTokenError):
            validate_secure_action_token(token + 'tamper', user=user, expected_purpose='RESET_PASSWORD')
        user.token_version += 1
        with self.assertRaises(SecureActionTokenError):
            validate_secure_action_token(token, user=user, expected_purpose='RESET_PASSWORD')

    def test_action_token_expiry(self):
        user = SimpleNamespace(pk=uuid.uuid4(), token_version=0)
        token = generate_secure_action_token(user, purpose='RESET_PASSWORD', lifetime=timedelta(seconds=-10))
        with self.assertRaises(SecureActionTokenError):
            validate_secure_action_token(token, user=user, expected_purpose='RESET_PASSWORD')

    def test_start_authorize_url_has_pkce_state_and_dedicated_callback(self):
        with patch('apps.accounts.user.github_auth_service.ClientGitHubOAuthState.objects.create') as create:
            url, browser_binding = start_oauth('signup')
        self.assertGreater(len(browser_binding), 40)
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, 'github.com')
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertEqual(params['prompt'], ['select_account'])
        self.assertEqual(params['redirect_uri'], ['https://auto.example.invalid/auth/github/callback'])
        self.assertEqual(len(params['state'][0]) > 35, True)
        self.assertNotIn(params['state'][0], create.call_args.kwargs['state_hash'])
        self.assertNotIn('code_verifier', params)
        self.assertNotIn(browser_binding, url)
        self.assertNotIn('testing-only-not-a-real-secret', url)

    def test_callback_is_not_project_callback(self):
        self.assertEqual(auth_callback_url(), 'https://auto.example.invalid/auth/github/callback')
    @override_settings(GITHUB_AUTH_CALLBACK_URL='https://backend.example.invalid/api/auth/github/callback/')
    def test_backend_legacy_url_migrates_to_frontend(self):
        self.assertEqual(auth_callback_url(), 'https://auto.example.invalid/auth/github/callback')

    @override_settings(GITHUB_AUTH_CALLBACK_URL='https://evil.example.invalid/auth/github/callback')
    def test_unexpected_callback_host_is_rejected(self):
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            auth_callback_url()


from django.test import TestCase
from rest_framework.exceptions import ValidationError
from apps.accounts.user.models import User
from apps.accounts.user.services import register_client
from apps.accounts.user.github_auth_service import finish_oauth, consume_login_ticket
from apps.accounts.user.github_auth_models import ClientGitHubIdentity


class GitHubAuthDatabaseTests(TestCase):
    """Tests nécessitant la base de test MySQL autorisée par Django."""

    def test_unlinked_existing_email_never_autolinks(self):
        local = register_client(email='existing@example.invalid', full_name='Existing User',
                                password='correct-local-password-very-long')
        local.is_email_verified = True
        local.save(update_fields=['is_email_verified', 'updated_at'])
        result, ticket = finish_oauth(intent='login', link_user=None, github_id=100100,
            browser_binding_hash='1'*64,
            github_login='verifiedgithub', email='existing@example.invalid', full_name='Other Name')
        self.assertEqual(result, 'account_exists')
        self.assertIsNone(ticket)
        self.assertFalse(ClientGitHubIdentity.objects.filter(user=local).exists())

    def test_signup_verifies_email_and_ticket_is_one_time(self):
        binding = 'signup-binding-secret-' + 'x'*40
        result, ticket = finish_oauth(intent='signup', link_user=None, github_id=100200,
            browser_binding_hash=__import__('hashlib').sha256(binding.encode()).hexdigest(),
            github_login='newgithub', email='new@example.invalid', full_name='A New Name')
        self.assertEqual(result, 'created')
        self.assertIsNotNone(ticket)
        user = User.objects.get(email='new@example.invalid')
        self.assertTrue(user.is_email_verified)
        self.assertTrue(user.check_password('irrelevant-password') is False)
        self.assertEqual(consume_login_ticket(ticket, binding).pk, user.pk)
        with self.assertRaises(ValidationError):
            consume_login_ticket(ticket, binding)

    def test_login_creates_github_account_when_verified_email_is_free(self):
        status, ticket = finish_oauth(intent='login', link_user=None, github_id=100401,
            browser_binding_hash='h' * 64, github_login='github-new-user',
            email='brand-new-login@example.invalid', full_name='New GitHub User')
        self.assertEqual(status, 'created')
        self.assertIsNotNone(ticket)
        self.assertTrue(User.objects.filter(email='brand-new-login@example.invalid').exists())

    def test_link_from_existing_account_then_login(self):
        user = register_client(email='link@example.invalid', full_name='Existing User',
                               password='correct-local-password-very-long')
        user.is_email_verified = True
        user.save(update_fields=['is_email_verified', 'updated_at'])
        binding = 'link-binding-secret-' + 'x'*40
        status, ticket = finish_oauth(intent='link', link_user=user, github_id=100300,
            browser_binding_hash=__import__('hashlib').sha256(binding.encode()).hexdigest(),
            github_login='linked-account', email='other-email@example.invalid', full_name='Linked')
        self.assertEqual(status, 'linked')
        self.assertIsNone(ticket)
        status, ticket = finish_oauth(intent='login', link_user=None, github_id=100300,
            browser_binding_hash=__import__('hashlib').sha256(binding.encode()).hexdigest(),
            github_login='linked-account', email='other-email@example.invalid', full_name='Linked')
        self.assertEqual(status, 'ok')
        self.assertEqual(consume_login_ticket(ticket, binding).pk, user.pk)
