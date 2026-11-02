import uuid
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class DeploymentConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Mode(models.TextChoices):
        DEVOPS = 'DEVOPS', 'DevOps'
        DEVSECOPS = 'DEVSECOPS', 'DevSecOps'
    class Builder(models.TextChoices):
        DOCKERFILE = 'DOCKERFILE', 'Dockerfile'
        BUILDPACK = 'BUILDPACK', 'Buildpack'
        NIXPACKS = 'NIXPACKS', 'Nixpacks'
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='deployment_configs')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='deployment_configs')
    version = models.PositiveIntegerField()
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.DEVSECOPS)
    builder = models.CharField(max_length=12, choices=Builder.choices, default=Builder.DOCKERFILE)
    repository_branch = models.CharField(max_length=250, default='main')
    root_directory = models.CharField(max_length=500, default='.')
    build_command = models.TextField(blank=True)
    start_command = models.TextField(blank=True)
    dockerfile_path = models.CharField(max_length=500, default='Dockerfile')
    internal_port = models.PositiveIntegerField(default=8000, validators=[MinValueValidator(1), MaxValueValidator(65535)])
    healthcheck_path = models.CharField(max_length=300, default='/')
    settings = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=False)
    class Meta:
        db_table = 'configurations_deploymentconfig'
        constraints = [models.UniqueConstraint(fields=['environment', 'version'], name='unique_config_version')]

    def __str__(self):
        return f'DeploymentConfig : {self.version}'

class ConfigValidationReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    configuration = models.ForeignKey('configurations.DeploymentConfig', on_delete=models.CASCADE, related_name='validation_reports')
    is_valid = models.BooleanField(default=False)
    checks = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    capability_snapshot = models.JSONField(default=dict)

    def __str__(self):
        return f'ConfigValidationReport : {self.pk}'

    class Meta:
        db_table = 'configurations_configvalidationreport'

class DeploymentTriggerRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Kind(models.TextChoices):
        MANUAL = 'MANUAL', 'Manuel'
        PUSH = 'PUSH', 'Push Git'
        SCHEDULE = 'SCHEDULE', 'Planifié'
    configuration = models.ForeignKey('configurations.DeploymentConfig', on_delete=models.CASCADE, related_name='triggers')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    branch_pattern = models.CharField(max_length=250, blank=True)
    schedule = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'DeploymentTriggerRule : {self.branch_pattern}'

    class Meta:
        db_table = 'configurations_deploymenttriggerrule'
