import uuid

from django.db import models


class Release(models.Model):
    class Status(models.TextChoices):
        READY = 'READY', 'Prête'
        ACTIVE = 'ACTIVE', 'Active'
        SUPERSEDED = 'SUPERSEDED', 'Remplacée'
        FAILED = 'FAILED', 'Échec'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='releases')
    source_deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.PROTECT, related_name='releases')
    previous_release = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    version = models.CharField(max_length=100)
    image_reference = models.CharField(max_length=500, blank=True)
    image_digest = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.environment} — release {self.version}'

    class Meta:
        db_table = 'releases_release'
        constraints = [models.UniqueConstraint(fields=['environment', 'version'], name='unique_release_version_env')]
