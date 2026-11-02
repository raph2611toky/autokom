import uuid
from django.db import models

class IntegrationAdapter(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    name = models.CharField(max_length=140, unique=True)
    provider = models.CharField(max_length=100)
    endpoint_url = models.URLField(max_length=700, blank=True)
    credentials_ref = models.CharField(max_length=350, blank=True)
    enabled = models.BooleanField(default=False)
    observed_status = models.CharField(max_length=30, default='UNKNOWN')
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'IntegrationAdapter : {self.name}'

    class Meta:
        db_table = 'infrastructure_integrationadapter'

class ToolCapability(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    adapter = models.ForeignKey('infrastructure.IntegrationAdapter', on_delete=models.CASCADE, related_name='capabilities')
    tool_name = models.CharField(max_length=120)
    capability_key = models.CharField(max_length=160)
    version = models.CharField(max_length=100, blank=True)
    is_configured = models.BooleanField(default=False)
    is_available = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=False)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = 'infrastructure_toolcapability'
        constraints = [models.UniqueConstraint(fields=['adapter', 'capability_key'], name='unique_adapter_capability')]

    def __str__(self):
        return f'ToolCapability : {self.version}'

class IntegrationHealthCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    adapter = models.ForeignKey('infrastructure.IntegrationAdapter', on_delete=models.CASCADE, related_name='health_checks')
    success = models.BooleanField(default=False)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    checked_at = models.DateTimeField()
    error_code = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return f'IntegrationHealthCheck : {self.pk}'

    class Meta:
        db_table = 'infrastructure_integrationhealthcheck'

class DeploymentPool(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    name = models.CharField(max_length=180, unique=True)
    kind = models.CharField(max_length=35, default='DOCKER_HOST')
    endpoint_ref = models.CharField(max_length=350, blank=True)
    cpu_capacity_millicores = models.PositiveIntegerField(default=0)
    memory_capacity_bytes = models.PositiveBigIntegerField(default=0)
    storage_capacity_bytes = models.PositiveBigIntegerField(default=0)
    is_enabled = models.BooleanField(default=False)
    status = models.CharField(max_length=30, default='UNKNOWN')
    restrictions = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'DeploymentPool : {self.name}'

    class Meta:
        db_table = 'infrastructure_deploymentpool'
