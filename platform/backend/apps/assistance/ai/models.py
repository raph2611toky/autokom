import uuid

from django.db import models


class AIInteraction(models.Model):
    class Kind(models.TextChoices):
        PROJECT_REVIEW = 'PROJECT_REVIEW', 'Analyse de projet'
        CONFIGURATION = 'CONFIGURATION', 'Configuration'
        DIAGNOSIS = 'DIAGNOSIS', 'Diagnostic'
        RESOURCES = 'RESOURCES', 'Ressources'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='ai_interactions')
    project = models.ForeignKey('projects.Project', on_delete=models.SET_NULL, null=True, blank=True, related_name='ai_interactions')
    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    provider_name = models.CharField(max_length=120, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    prompt_redacted = models.TextField(blank=True)
    response_redacted = models.TextField(blank=True)
    status = models.CharField(max_length=30, default='COMPLETED')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.kind} — {self.pk}'

    class Meta:
        db_table = 'assistant_aiinteraction'


class AISuggestion(models.Model):
    class Status(models.TextChoices):
        PROPOSED = 'PROPOSED', 'Proposée'
        ACCEPTED = 'ACCEPTED', 'Acceptée'
        REJECTED = 'REJECTED', 'Refusée'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    interaction = models.ForeignKey('assistant.AIInteraction', on_delete=models.CASCADE, related_name='suggestions')
    title = models.CharField(max_length=220)
    explanation = models.TextField(blank=True)
    proposed_changes = models.JSONField(default=dict, help_text='Une proposition n’est jamais exécutée automatiquement.')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PROPOSED)
    reviewed_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.title} ({self.status})'

    class Meta:
        db_table = 'assistant_aisuggestion'
