import uuid

from django.db import models


class DeploymentRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'En attente'
        RUNNING = 'RUNNING', 'En cours'
        SUCCEEDED = 'SUCCEEDED', 'Réussi'
        FAILED = 'FAILED', 'Échec'
        CANCELLED = 'CANCELLED', 'Annulé'
        BLOCKED = 'BLOCKED', 'Bloqué'
    class Trigger(models.TextChoices):
        MANUAL = 'MANUAL', 'Manuel'
        GIT_PUSH = 'GIT_PUSH', 'Push Git'
        REDEPLOY = 'REDEPLOY', 'Redéploiement'
        ROLLBACK = 'ROLLBACK', 'Restauration'
    class Mode(models.TextChoices):
        DEVOPS = 'DEVOPS', 'DevOps'
        DEVSECOPS = 'DEVSECOPS', 'DevSecOps'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='deployment_runs')
    configuration = models.ForeignKey('configurations.DeploymentConfig', on_delete=models.PROTECT, related_name='runs')
    entitlement = models.ForeignKey('plan_entitlements.ApplicationEntitlement', on_delete=models.PROTECT, related_name='deployment_runs')
    requested_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='deployment_requests')
    webhook_event = models.ForeignKey('integrations.GitWebhookEvent', on_delete=models.SET_NULL, null=True, blank=True)
    trigger_type = models.CharField(max_length=16, choices=Trigger.choices, default=Trigger.MANUAL)
    mode_snapshot = models.CharField(max_length=12, choices=Mode.choices, default=Mode.DEVOPS)
    resources_snapshot = models.JSONField(default=dict, help_text='CPU / RAM / stockage effectivement autorisés au lancement.')
    commit_sha = models.CharField(max_length=64, blank=True)
    branch = models.CharField(max_length=255)
    external_run_id = models.CharField(max_length=180, blank=True)
    ci_provider = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(max_length=150, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.environment} — {self.trigger_type} ({self.status})'

    class Meta:
        db_table = 'deployments_deploymentrun'
        indexes = [models.Index(fields=['environment', 'created_at'])]


class PipelineStageRun(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'En attente'
        RUNNING = 'RUNNING', 'En cours'
        SUCCESS = 'SUCCESS', 'Réussie'
        FAILED = 'FAILED', 'Échec'
        SKIPPED = 'SKIPPED', 'Ignorée'
        BLOCKED = 'BLOCKED', 'Bloquée'
        CANCELLED = 'CANCELLED', 'Annulée'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.CASCADE, related_name='stages')
    stage_key = models.CharField(max_length=120)
    name = models.CharField(max_length=160)
    position = models.PositiveIntegerField()
    tool_name = models.CharField(max_length=120, blank=True)
    external_stage_id = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    logs_reference = models.CharField(max_length=500, blank=True)
    summary = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.deployment_id} — {self.name}'

    class Meta:
        db_table = 'deployments_pipelinestagerun'
        constraints = [models.UniqueConstraint(fields=['deployment', 'stage_key'], name='unique_stage_key_per_deployment')]
        ordering = ['position']


class PipelineFinding(models.Model):
    class Severity(models.TextChoices):
        INFO = 'INFO', 'Information'
        LOW = 'LOW', 'Faible'
        MEDIUM = 'MEDIUM', 'Moyenne'
        HIGH = 'HIGH', 'Élevée'
        CRITICAL = 'CRITICAL', 'Critique'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stage = models.ForeignKey('deployments.PipelineStageRun', on_delete=models.CASCADE, related_name='findings')
    scanner = models.CharField(max_length=100)
    rule_id = models.CharField(max_length=200)
    severity = models.CharField(max_length=12, choices=Severity.choices)
    title = models.CharField(max_length=260)
    location = models.CharField(max_length=500, blank=True)
    details = models.TextField(blank=True)
    report_reference = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.scanner}: {self.rule_id} ({self.severity})'

    class Meta:
        db_table = 'deployments_pipelinefinding'
