import uuid
from django.db import models

class BuildArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.PROTECT, related_name='artifacts')
    kind = models.CharField(max_length=40, default='OCI_IMAGE')
    name = models.CharField(max_length=250)
    immutable_digest = models.CharField(max_length=200)
    registry_url = models.CharField(max_length=700, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    retain_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'BuildArtifact : {self.name}'

    class Meta:
        db_table = 'releases_buildartifact'

class Release(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='releases')
    artifact = models.ForeignKey('releases.BuildArtifact', on_delete=models.PROTECT, related_name='releases')
    source_deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.PROTECT, related_name='releases')
    version_label = models.CharField(max_length=180)
    status = models.CharField(max_length=20, default='READY')
    published_at = models.DateTimeField(null=True, blank=True)
    previous_release = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    class Meta:
        db_table = 'releases_release'
        constraints = [models.UniqueConstraint(fields=['environment', 'version_label'], name='unique_release_version')]

    def __str__(self):
        return f'Release : {self.status}'
