import uuid
from django.db import models

class MonitoringRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='monitoring_rules')
    metric_name = models.CharField(max_length=150)
    operator = models.CharField(max_length=4, default='GT')
    threshold = models.DecimalField(max_digits=18, decimal_places=6)
    evaluation_seconds = models.PositiveIntegerField(default=300)
    severity = models.CharField(max_length=15, default='WARNING')
    is_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f'MonitoringRule : {self.metric_name}'

    class Meta:
        db_table = 'monitoring_monitoringrule'

class MonitoringAlert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    rule = models.ForeignKey('monitoring.MonitoringRule', on_delete=models.PROTECT, related_name='alerts')
    status = models.CharField(max_length=20, default='FIRING')
    fired_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'MonitoringAlert : {self.status}'

    class Meta:
        db_table = 'monitoring_monitoringalert'

class HealthSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='health_snapshots')
    status = models.CharField(max_length=24, default='UNKNOWN')
    observed_at = models.DateTimeField()
    source = models.CharField(max_length=130, blank=True)
    summary = models.JSONField(default=dict)

    def __str__(self):
        return f'HealthSnapshot : {self.status}'

    class Meta:
        db_table = 'monitoring_healthsnapshot'
