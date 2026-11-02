import uuid
from django.db import models

class GitConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Provider(models.TextChoices):
        GITHUB = 'GITHUB', 'GitHub'
        GITLAB = 'GITLAB', 'GitLab'
        GENERIC = 'GENERIC', 'Git'
    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='git_connections')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='git_connections')
    provider = models.CharField(max_length=12, choices=Provider.choices)
    external_account_id = models.CharField(max_length=255, blank=True)
    account_name = models.CharField(max_length=255)
    credentials_ref = models.CharField(max_length=255, blank=True, help_text='Référence opaque vers le coffre de secrets')
    permissions = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'GitConnection : {self.account_name}'

    class Meta:
        db_table = 'integrations_gitconnection'

class GitRepository(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    connection = models.ForeignKey('integrations.GitConnection', on_delete=models.CASCADE, related_name='repositories')
    external_id = models.CharField(max_length=255)
    full_name = models.CharField(max_length=350)
    clone_url = models.URLField(max_length=700)
    web_url = models.URLField(max_length=700, blank=True)
    default_branch = models.CharField(max_length=180, default='main')
    is_private = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = 'integrations_gitrepository'
        constraints = [models.UniqueConstraint(fields=['connection', 'external_id'], name='unique_git_repository')]

    def __str__(self):
        return f'GitRepository : {self.full_name}'

class WebhookEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    connection = models.ForeignKey('integrations.GitConnection', on_delete=models.CASCADE, related_name='webhooks')
    provider_hook_id = models.CharField(max_length=255, blank=True)
    event_types = models.JSONField(default=list)
    signing_secret_ref = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'WebhookEndpoint : {self.pk}'

    class Meta:
        db_table = 'integrations_webhookendpoint'

class WebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    endpoint = models.ForeignKey('integrations.WebhookEndpoint', on_delete=models.CASCADE, related_name='deliveries')
    external_event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=120)
    payload_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=24, default='RECEIVED')
    processed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=120, blank=True)
    class Meta:
        db_table = 'integrations_webhookdelivery'
        constraints = [models.UniqueConstraint(fields=['endpoint', 'external_event_id'], name='unique_webhook_delivery')]

    def __str__(self):
        return f'WebhookDelivery : {self.status}'
