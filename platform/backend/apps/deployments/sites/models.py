import uuid
from django.db import models

class HostedApplication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.OneToOneField('environments.Environment', on_delete=models.PROTECT, related_name='hosted_application')
    current_release = models.ForeignKey('releases.Release', on_delete=models.PROTECT, null=True, blank=True, related_name='current_sites')
    deployment_pool = models.ForeignKey('infrastructure.DeploymentPool', on_delete=models.PROTECT, related_name='hosted_apps')
    runtime_id = models.CharField(max_length=250, blank=True)
    desired_state = models.CharField(max_length=30, default='RUNNING')
    observed_state = models.CharField(max_length=30, default='UNKNOWN')
    last_observed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'HostedApplication : {self.pk}'

    class Meta:
        db_table = 'sites_hostedapplication'

class SiteHealthCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    application = models.ForeignKey('sites.HostedApplication', on_delete=models.CASCADE, related_name='healthchecks')
    protocol = models.CharField(max_length=12, default='HTTP')
    path = models.CharField(max_length=255, default='/')
    timeout_seconds = models.PositiveSmallIntegerField(default=10)
    last_status = models.CharField(max_length=20, default='UNKNOWN')
    checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=250, blank=True)

    def __str__(self):
        return f'SiteHealthCheck : {self.pk}'

    class Meta:
        db_table = 'sites_sitehealthcheck'

class SiteAction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    application = models.ForeignKey('sites.HostedApplication', on_delete=models.CASCADE, related_name='actions')
    requested_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='site_actions')
    operation = models.ForeignKey('operations.AsyncOperation', on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=24)
    status = models.CharField(max_length=20, default='PENDING')
    result = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'SiteAction : {self.status}'

    class Meta:
        db_table = 'sites_siteaction'
