import uuid

from django.db import models


class HostedApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'En attente'
        STARTING = 'STARTING', 'Démarrage'
        RUNNING = 'RUNNING', 'En service'
        STOPPED = 'STOPPED', 'Arrêtée'
        ERROR = 'ERROR', 'Erreur'
        UNKNOWN = 'UNKNOWN', 'Inconnu'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.OneToOneField('environments.Environment', on_delete=models.PROTECT, related_name='hosted_application')
    current_release = models.ForeignKey('releases.Release', on_delete=models.SET_NULL, null=True, blank=True, related_name='hosted_sites')
    runtime_id = models.CharField(max_length=255, blank=True, help_text='Identifiant logique communiqué par le moteur, pas accès au serveur.')
    desired_state = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    observed_state = models.CharField(max_length=12, choices=Status.choices, default=Status.UNKNOWN)
    last_health_checked_at = models.DateTimeField(null=True, blank=True)
    health_status = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.environment} ({self.observed_state})'

    class Meta:
        db_table = 'sites_hostedapplication'
