from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.accounts.user.models import User
from apps.projects.git.models import GitHubOAuthState
from apps.projects.git.services import consume_state, verify_signature
from helpers.security import token_digest


class GitHubOAuthSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='git@example.org', full_name='Git', password='not-a-real-password-strong')

    def test_oauth_state_is_single_use(self):
        GitHubOAuthState.objects.create(user=self.user, state_hash=token_digest('state-unique'),
                                        expires_at=timezone.now() + timedelta(minutes=5))
        self.assertEqual(consume_state('state-unique').id, self.user.id)
        with self.assertRaises(ValidationError):
            consume_state('state-unique')

    @override_settings(GITHUB_WEBHOOK_SECRET="It's a Secret to Everybody")
    def test_webhook_signature_matches_github_official_vector(self):
        verify_signature(b'Hello, World!', 'sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17')
        with self.assertRaises(PermissionDenied):
            verify_signature(b'Hello, World!', 'sha256=00000')
