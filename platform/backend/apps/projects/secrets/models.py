import uuid
from django.db import models


class SecretReference(models.Model):
    class Scope(models.TextChoices):
        PROJECT = 'PROJECT', 'Projet'
        ENVIRONMENT = 'ENVIRONMENT', 'Environnement'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, related_name='secret_references')
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='secrets')
    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE, null=True, blank=True, related_name='secrets')
    name = models.CharField(max_length=160)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.ENVIRONMENT)
    vault_reference = models.CharField(max_length=500, blank=True, help_text='Référence future vers un coffre externe.')
    encrypted_value = models.TextField(blank=True, help_text='Valeur chiffrée Fernet ; jamais retournée par API.')
    version_ref = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.scope == self.Scope.ENVIRONMENT and not self.environment_id:
            raise ValidationError({'environment': 'Un secret ENVIRONMENT exige un environnement.'})
        if self.scope == self.Scope.PROJECT and self.environment_id:
            raise ValidationError({'environment': 'Un secret PROJECT ne cible pas un environnement.'})
        if self.environment_id and self.project_id and self.environment.project_id != self.project_id:
            raise ValidationError({'environment': 'Environnement étranger au projet.'})
        if self.project_id and self.organization_id and self.project.organization_id != self.organization_id:
            raise ValidationError({'organization': 'Organisation étrangère au projet.'})
        if bool(self.vault_reference) == bool(self.encrypted_value):
            raise ValidationError('Indiquer une valeur chiffrée OU une référence à un coffre.')

    def __str__(self):
        return f'{self.project.slug} / {self.name}'

    class Meta:
        db_table = 'secrets_secretreference'
        constraints = [models.UniqueConstraint(fields=['project', 'environment', 'name'], name='unique_secret_project_env_name')]
