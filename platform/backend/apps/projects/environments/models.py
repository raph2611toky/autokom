import uuid
from django.db import models
from django.core.exceptions import ValidationError

class Environment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Kind(models.TextChoices):
        DEVELOPMENT = 'DEVELOPMENT', 'Développement'
        STAGING = 'STAGING', 'Préproduction'
        PRODUCTION = 'PRODUCTION', 'Production'
        PREVIEW = 'PREVIEW', 'Prévisualisation'
    project = models.ForeignKey('projects.Project', on_delete=models.PROTECT, related_name='environments')
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.DEVELOPMENT)
    deployment_pool = models.ForeignKey('infrastructure.DeploymentPool', on_delete=models.PROTECT, null=True, blank=True, related_name='environments')
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'environments_environment'
        constraints = [models.UniqueConstraint(fields=['project', 'slug'], name='unique_environment_slug')]

    def __str__(self):
        return f'Environment : {self.name}'

class EnvironmentVariable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='variables')
    name = models.CharField(max_length=180)
    plain_value = models.TextField(blank=True, help_text='Interdit pour les secrets')
    secret = models.ForeignKey('secrets.SecretReference', on_delete=models.PROTECT, null=True, blank=True, related_name='env_variables')
    is_sensitive = models.BooleanField(default=False)

    def clean(self):
        super().clean()
        if self.is_sensitive and self.plain_value:
            raise ValidationError({'plain_value': 'Un secret ne doit pas être stocké en clair.'})
        if self.plain_value and self.secret_id:
            raise ValidationError({'secret': 'Une variable ne peut avoir une valeur claire et une référence de secret.'})

    class Meta:
        db_table = 'environments_environmentvariable'
        constraints = [models.UniqueConstraint(fields=['environment', 'name'], name='unique_env_variable')]

    def __str__(self):
        return f'EnvironmentVariable : {self.name}'

class EnvironmentBranchRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='branch_rules')
    repository = models.ForeignKey('integrations.GitRepository', on_delete=models.PROTECT, related_name='environment_rules')
    branch_pattern = models.CharField(max_length=250)
    auto_deploy = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'EnvironmentBranchRule : {self.branch_pattern}'

    class Meta:
        db_table = 'environments_environmentbranchrule'
