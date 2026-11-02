import uuid

from django.db import models


class DeploymentConfig(models.Model):
    class BuildMethod(models.TextChoices):
        DOCKERFILE = 'DOCKERFILE', 'Dockerfile'
        BUILDPACKS = 'BUILDPACKS', 'Buildpacks'
        STATIC = 'STATIC', 'Site statique'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='deployment_configs')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='deployment_configs')
    version = models.PositiveIntegerField()
    is_draft = models.BooleanField(default=True)
    build_method = models.CharField(max_length=16, choices=BuildMethod.choices, default=BuildMethod.DOCKERFILE)
    branch = models.CharField(max_length=255, default='main')
    root_directory = models.CharField(max_length=300, blank=True)
    dockerfile_path = models.CharField(max_length=300, default='Dockerfile')
    build_command = models.CharField(max_length=500, blank=True)
    start_command = models.CharField(max_length=500, blank=True)
    output_directory = models.CharField(max_length=300, blank=True)
    internal_port = models.PositiveIntegerField(default=8000)
    healthcheck_path = models.CharField(max_length=250, default='/')
    auto_deploy = models.BooleanField(default=False)
    auto_deploy_branch = models.CharField(max_length=255, blank=True)
    settings = models.JSONField(default=dict, blank=True, help_text='Données non sensibles validées par le service.')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.environment} — config v{self.version}'

    class Meta:
        db_table = 'configurations_deploymentconfig'
        constraints = [models.UniqueConstraint(fields=['environment', 'version'], name='unique_deployment_config_version')]
