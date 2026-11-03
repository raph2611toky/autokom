import uuid

from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.db.models.functions import Lower
from django.utils import timezone
from apps.accounts.user.managers import ClientUserManager


class User(models.Model):
    """Identité cliente distincte du compte système de Django."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=180)
    password = models.CharField(max_length=128)
    avatar = models.ImageField(upload_to='users/avatars/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    token_version = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ClientUserManager()

    def set_password(self, password):
        self.password = make_password(password)

    def check_password(self, password):
        return check_password(password, self.password)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return self.email

    class Meta:
        db_table = 'accounts_user'
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_lower_client_email')]


class UserActionToken(models.Model):
    """Un jeton à usage unique : activation d'email OU réinitialisation."""
    class Purpose(models.TextChoices):
        VERIFY_EMAIL = 'VERIFY_EMAIL', 'Vérifier l’email'
        RESET_PASSWORD = 'RESET_PASSWORD', 'Réinitialiser le mot de passe'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='action_tokens')
    purpose = models.CharField(max_length=24, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.email} — {self.get_purpose_display()}'

    class Meta:
        db_table = 'accounts_useractiontoken'
        indexes = [models.Index(fields=['user', 'purpose', 'consumed_at'])]


class UserSession(models.Model):
    """Session renouvelable du client; jeton JWT représenté par une empreinte."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='sessions')
    jti_hash = models.CharField(max_length=64, unique=True)
    token_family = models.UUIDField(default=uuid.uuid4, db_index=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_valid(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f'{self.user.email} — session {self.id}'

    class Meta:
        db_table = 'accounts_usersession'
        indexes = [models.Index(fields=['user', 'revoked_at', 'expires_at'])]


# Import explicite pour que Django découvre les modèles de l'auth GitHub.
from apps.accounts.user.github_auth_models import (  # noqa: E402, F401
    ClientGitHubIdentity, ClientGitHubOAuthState, ClientGitHubLoginTicket,
)
