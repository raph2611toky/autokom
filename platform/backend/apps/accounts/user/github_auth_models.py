"""Modèles OAuth de connexion client : indépendants de projects.git."""
import uuid
from django.db import models


class ClientGitHubIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField('accounts.User', related_name='github_identity', on_delete=models.CASCADE)
    github_user_id = models.PositiveBigIntegerField(unique=True)
    github_login = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_clientgithubidentity'


class ClientGitHubOAuthState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state_hash = models.CharField(max_length=64, unique=True)
    browser_binding_hash = models.CharField(max_length=64)
    intent = models.CharField(max_length=12)  # login, signup, link
    link_user = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.CASCADE,
                                  related_name='github_auth_states')
    pkce_verifier_ciphertext = models.TextField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'accounts_clientgithubstate'


class ClientGitHubLoginTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    browser_binding_hash = models.CharField(max_length=64)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='github_login_tickets')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'accounts_clientgithubticket'
