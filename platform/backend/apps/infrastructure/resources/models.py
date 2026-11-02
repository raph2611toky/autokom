import uuid
from django.db import models

class ResourcePlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    name = models.CharField(max_length=120, unique=True)
    cpu_millicores = models.PositiveIntegerField(default=1000)
    memory_bytes = models.PositiveBigIntegerField(default=1073741824)
    storage_bytes = models.PositiveBigIntegerField(default=10737418240)
    max_instances = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return f'ResourcePlan : {self.name}'

    class Meta:
        db_table = 'resources_resourceplan'

class QuotaAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='quota_assignments')
    plan = models.ForeignKey('resources.ResourcePlan', on_delete=models.PROTECT, related_name='assignments')
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(null=True, blank=True)
    limits_override = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'QuotaAssignment : {self.pk}'

    class Meta:
        db_table = 'resources_quotaassignment'

class ResourceAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='resource_allocations')
    pool = models.ForeignKey('infrastructure.DeploymentPool', on_delete=models.PROTECT, related_name='resource_allocations')
    cpu_millicores = models.PositiveIntegerField()
    memory_bytes = models.PositiveBigIntegerField()
    storage_bytes = models.PositiveBigIntegerField(default=0)
    replicas = models.PositiveSmallIntegerField(default=1)
    activated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'ResourceAllocation : {self.pk}'

    class Meta:
        db_table = 'resources_resourceallocation'

class ResourceReservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='reservations')
    pool = models.ForeignKey('infrastructure.DeploymentPool', on_delete=models.PROTECT)
    cpu_millicores = models.PositiveIntegerField()
    memory_bytes = models.PositiveBigIntegerField()
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, default='RESERVED')

    def __str__(self):
        return f'ResourceReservation : {self.status}'

    class Meta:
        db_table = 'resources_resourcereservation'

class ResourceUsageRollup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='usage_rollups')
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    cpu_avg_millicores = models.PositiveIntegerField(default=0)
    memory_peak_bytes = models.PositiveBigIntegerField(default=0)
    network_bytes = models.PositiveBigIntegerField(default=0)
    metrics_source = models.CharField(max_length=120, blank=True)
    class Meta:
        db_table = 'resources_resourceusagerollup'
        constraints = [models.UniqueConstraint(fields=['environment', 'period_start', 'period_end'], name='unique_usage_rollup_period')]

    def __str__(self):
        return f'ResourceUsageRollup : {self.pk}'
