import uuid

from django.db import models


class ApplicationEntitlement(models.Model):
    """Droit accordé par application/environnement ; les sélections frontend ne donnent aucun droit."""
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Actif'
        PENDING = 'PENDING', 'En attente'
        REVOKED = 'REVOKED', 'Révoqué'
        EXPIRED = 'EXPIRED', 'Expiré'
    class GrantSource(models.TextChoices):
        SYSTEM = 'SYSTEM', 'Allocation gratuite automatique'
        ADMIN = 'ADMIN', 'Attribution manuelle par administrateur'
        PAYMENT = 'PAYMENT', 'Paiement futur (non intégré)'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.OneToOneField('environments.Environment', on_delete=models.PROTECT, related_name='entitlement')
    mode_offer = models.ForeignKey('plan_catalog.DeploymentModeOffer', on_delete=models.PROTECT, related_name='entitlements')
    resource_plan = models.ForeignKey('plan_catalog.ResourcePlan', on_delete=models.PROTECT, related_name='entitlements')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    grant_source = models.CharField(max_length=12, choices=GrantSource.choices, default=GrantSource.SYSTEM)
    approved_by = models.ForeignKey('administrators.Administrator', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_entitlements')
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.status == self.Status.ACTIVE and self.grant_source == self.GrantSource.SYSTEM:
            if (self.mode_offer_id and self.mode_offer.is_premium) or (self.resource_plan_id and self.resource_plan.is_premium):
                raise ValidationError('Un droit premium ne peut pas être attribué automatiquement comme offre gratuite.')
        if self.status == self.Status.ACTIVE and self.grant_source == self.GrantSource.ADMIN and not self.approved_by_id:
            raise ValidationError({'approved_by': 'Une activation manuelle requiert un administrateur.'})
        if self.status == self.Status.ACTIVE and self.grant_source == self.GrantSource.PAYMENT:
            raise ValidationError({'grant_source': 'Le paiement n’est pas encore intégré : activation interdite.'})

    def __str__(self):
        return f'{self.environment} — {self.mode_offer.code} / {self.resource_plan.code} ({self.status})'

    class Meta:
        db_table = 'plans_applicationentitlement'
