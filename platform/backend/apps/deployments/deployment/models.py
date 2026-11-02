import uuid
from django.db import models

class DeploymentRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'En attente'
        RUNNING = 'RUNNING', 'En cours'
        SUCCESS = 'SUCCESS', 'Réussi'
        FAILED = 'FAILED', 'Échoué'
        BLOCKED = 'BLOCKED', 'Bloqué'
        CANCELED = 'CANCELED', 'Annulé'
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='deployment_runs')
    configuration = models.ForeignKey('configurations.DeploymentConfig', on_delete=models.PROTECT, related_name='deployment_runs')
    policy_version = models.ForeignKey('policies.PipelinePolicyVersion', on_delete=models.PROTECT, null=True, blank=True, related_name='deployment_runs')
    requested_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True, related_name='deployment_requests')
    commit_sha = models.CharField(max_length=64, blank=True)
    branch = models.CharField(max_length=250, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    idempotency_key = models.CharField(max_length=150, unique=True)
    capabilities_snapshot = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f'DeploymentRun : {self.status}'

    class Meta:
        db_table = 'deployments_deploymentrun'

class DeploymentApproval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.CASCADE, related_name='approvals')
    stage_key = models.CharField(max_length=160)
    decided_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True, related_name='deployment_approvals')
    decision = models.CharField(max_length=15, default='PENDING')
    reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'DeploymentApproval : {self.pk}'

    class Meta:
        db_table = 'deployments_deploymentapproval'

class DeploymentTriggerEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.CASCADE, related_name='trigger_events')
    source = models.CharField(max_length=20, default='MANUAL')
    external_event_id = models.CharField(max_length=250, blank=True)
    commit_sha = models.CharField(max_length=64, blank=True)
    actor = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    payload_digest = models.CharField(max_length=64, blank=True)

    def __str__(self):
        return f'DeploymentTriggerEvent : {self.external_event_id}'

    class Meta:
        db_table = 'deployments_deploymenttriggerevent'
