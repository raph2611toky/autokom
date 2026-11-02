import uuid
from django.db import models

class AIProviderConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    provider = models.CharField(max_length=80)
    model_name = models.CharField(max_length=160)
    api_key_ref = models.CharField(max_length=350, blank=True)
    is_enabled = models.BooleanField(default=False)
    request_limit_per_hour = models.PositiveIntegerField(default=100)
    privacy_options = models.JSONField(default=dict)

    def __str__(self):
        return f'AIProviderConfig : {self.provider}'

    class Meta:
        db_table = 'assistant_aiproviderconfig'

class AIInteraction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='ai_interactions')
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, null=True, blank=True)
    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.SET_NULL, null=True, blank=True)
    provider = models.ForeignKey('assistant.AIProviderConfig', on_delete=models.PROTECT, null=True, blank=True)
    task = models.CharField(max_length=90)
    status = models.CharField(max_length=30, default='QUEUED')
    prompt_digest = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_estimate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)

    def __str__(self):
        return f'AIInteraction : {self.provider}'

    class Meta:
        db_table = 'assistant_aiinteraction'

class AISuggestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    interaction = models.ForeignKey('assistant.AIInteraction', on_delete=models.CASCADE, related_name='suggestions')
    kind = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    rationale = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='PROPOSED')
    reviewed_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'AISuggestion : {self.kind}'

    class Meta:
        db_table = 'assistant_aisuggestion'
