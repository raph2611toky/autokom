import uuid
from django.db import models


class DeploymentConfig(models.Model):
    class BuildMethod(models.TextChoices):
        AUTO = 'AUTO', 'Détection automatique'
        DOCKERFILE = 'DOCKERFILE', 'Dockerfile'
        BUILDPACKS = 'BUILDPACKS', 'Buildpacks'
        STATIC = 'STATIC', 'Site statique'

    class RuntimeType(models.TextChoices):
        PYTHON = 'PYTHON', 'Python'
        NODE = 'NODE', 'Node.js'
        STATIC = 'STATIC', 'Site statique'
        JAVA = 'JAVA', 'Java'
        PHP = 'PHP', 'PHP'
        OTHER = 'OTHER', 'Autre'

    class PipelineMode(models.TextChoices):
        DEVOPS = 'DEVOPS', 'DevOps'
        DEVSECOPS = 'DEVSECOPS', 'DevSecOps'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='deployment_configs')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='deployment_configs')
    version = models.PositiveIntegerField()
    is_draft = models.BooleanField(default=True)
    mode = models.CharField(max_length=12, choices=PipelineMode.choices, default='DEVOPS')
    resource_plan = models.ForeignKey('plan_catalog.ResourcePlan', on_delete=models.PROTECT, null=True, blank=True, related_name='deployment_configs')
    runtime_type = models.CharField(max_length=12, choices=RuntimeType.choices, default=RuntimeType.NODE)
    build_method = models.CharField(max_length=16, choices=BuildMethod.choices, default=BuildMethod.AUTO)
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
    settings = models.JSONField(default=dict, blank=True, help_text='Métadonnées non sensibles uniquement.')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.environment} — config v{self.version} ({self.runtime_type})'

    class Meta:
        db_table = 'configurations_deploymentconfig'
        constraints = [models.UniqueConstraint(fields=['environment', 'version'], name='unique_deployment_config_version')]


class ServiceStack(models.Model):
    """Opt-in frontend+backend deployment using the existing isolated environments.

    Legacy one-service projects have no ServiceStack and are not affected.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField('projects.Project', on_delete=models.PROTECT, related_name='service_stack')
    frontend_environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT,
                                             related_name='frontend_stacks')
    backend_environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT,
                                            related_name='backend_stacks')
    auto_deploy = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'configurations_servicestack'
