import uuid

from django.db import models


class Environment(models.Model):
    class Kind(models.TextChoices):
        PRODUCTION = 'PRODUCTION', 'Production'
        STAGING = 'STAGING', 'Préproduction'
        DEVELOPMENT = 'DEVELOPMENT', 'Développement'
        PREVIEW = 'PREVIEW', 'Prévisualisation'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.PROTECT, related_name='environments')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.PRODUCTION)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.project.slug} / {self.name}'

    class Meta:
        db_table = 'environments_environment'
        constraints = [models.UniqueConstraint(fields=['project', 'slug'], name='unique_env_per_project')]


class EnvironmentVariable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, related_name='variables')
    key = models.CharField(max_length=160)
    value = models.TextField(blank=True, help_text='Valeur non sensible uniquement.')
    secret = models.ForeignKey('secrets.SecretReference', on_delete=models.PROTECT, null=True, blank=True, related_name='environment_variables')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.secret_id and self.value:
            raise ValidationError({'value': 'Une variable secrète ne doit pas porter une valeur en clair.'})
        if self.secret_id and self.environment_id and self.secret.environment_id and self.secret.environment_id != self.environment_id:
            raise ValidationError({'secret': 'Le secret appartient à un autre environnement.'})

    def __str__(self):
        return f'{self.environment} — {self.key}'

    class Meta:
        db_table = 'environments_environmentvariable'
        constraints = [models.UniqueConstraint(fields=['environment', 'key'], name='unique_environment_variable')]
