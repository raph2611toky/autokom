import uuid

from django.db import models


class GitConnection(models.Model):
    class Provider(models.TextChoices):
        GITHUB = 'GITHUB', 'GitHub'
        GITLAB = 'GITLAB', 'GitLab'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='git_connections')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='git_connections')
    provider = models.CharField(max_length=16, choices=Provider.choices)
    external_account_id = models.CharField(max_length=160)
    account_name = models.CharField(max_length=180)
    credential_ref = models.CharField(max_length=255, blank=True, help_text='Référence au coffre de secrets, jamais le jeton en clair.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.get_provider_display()} : {self.account_name}'

    class Meta:
        db_table = 'integrations_gitconnection'
        constraints = [models.UniqueConstraint(fields=['organization', 'provider', 'external_account_id'], name='unique_git_account_per_org')]


class GitWebhookEvent(models.Model):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Reçu'
        HANDLED = 'HANDLED', 'Traité'
        IGNORED = 'IGNORED', 'Ignoré'
        FAILED = 'FAILED', 'Échec'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey('integrations.GitConnection', on_delete=models.CASCADE, related_name='events')
    external_event_id = models.CharField(max_length=255)
    repository_external_id = models.CharField(max_length=160)
    repository_full_name = models.CharField(max_length=300)
    event_type = models.CharField(max_length=40)
    branch = models.CharField(max_length=255, blank=True)
    commit_sha = models.CharField(max_length=64, blank=True)
    signature_verified = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.repository_full_name} / {self.external_event_id}'

    class Meta:
        db_table = 'integrations_gitwebhookevent'
        constraints = [models.UniqueConstraint(fields=['connection', 'external_event_id'], name='unique_git_event_delivery')]
