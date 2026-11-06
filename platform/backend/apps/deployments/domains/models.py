import uuid

from django.db import models


class Domain(models.Model):
    class Type(models.TextChoices):
        SUBDOMAIN = 'SUBDOMAIN', 'Sous-domaine AUTOKOM'
        CUSTOM = 'CUSTOM', 'Domaine personnel'
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'En attente'
        VERIFYING = 'VERIFYING', 'Vérification'
        ACTIVE = 'ACTIVE', 'Actif'
        ERROR = 'ERROR', 'Erreur'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, related_name='domains')
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='domains')
    hostname = models.CharField(max_length=253, unique=True)
    domain_type = models.CharField(max_length=16, choices=Type.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    verification_token_hash = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    tls_status = models.CharField(max_length=40, blank=True)
    is_primary = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    activation_error_code = models.CharField(max_length=90, blank=True)
    last_activation_attempt_at = models.DateTimeField(null=True, blank=True)
    certificate_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.hostname

    class Meta:
        db_table = 'domains_domain'
        constraints = [models.UniqueConstraint(fields=['environment'], condition=models.Q(is_primary=True), name='autokom_one_primary_domain_per_env')]
