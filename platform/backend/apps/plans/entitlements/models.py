import uuid

from django.db import models
from django.utils import timezone


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
        TEST = 'TEST', 'Abonnement simulé (sans transaction réelle)'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.OneToOneField('environments.Environment', on_delete=models.PROTECT, related_name='entitlement')
    mode_offer = models.ForeignKey('plan_catalog.DeploymentModeOffer', on_delete=models.PROTECT, related_name='entitlements')
    resource_plan = models.ForeignKey('plan_catalog.ResourcePlan', on_delete=models.PROTECT, related_name='entitlements')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    grant_source = models.CharField(max_length=12, choices=GrantSource.choices, default=GrantSource.SYSTEM)
    approved_by = models.ForeignKey('administrators.Administrator', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_entitlements')
    sandbox_checkout = models.ForeignKey('SandboxCheckout', on_delete=models.SET_NULL, null=True, blank=True, related_name='entitlements')
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
            raise ValidationError({'grant_source': 'Aucun fournisseur réel de paiement n’est encore connecté.'})
        if self.status == self.Status.ACTIVE and self.grant_source == self.GrantSource.TEST:
            checkout = self.sandbox_checkout if self.sandbox_checkout_id else None
            if (checkout is None or checkout.status != SandboxCheckout.Status.ACCEPTED or
                    checkout.environment_id != self.environment_id or
                    checkout.resource_plan_id != self.resource_plan_id or
                    checkout.expires_at <= timezone.now()):
                raise ValidationError({'sandbox_checkout': 'Reçu de test actif, conforme au plan et à l’environnement requis.'})

    def __str__(self):
        return f'{self.environment} — {self.mode_offer.code} / {self.resource_plan.code} ({self.status})'

    class Meta:
        db_table = 'plans_applicationentitlement'


class HighAvailabilityOption(models.Model):
    """Paid feature REQUEST, never proof of payment or right to provision replicas."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'En attente de validation commerciale'
        ACTIVE = 'ACTIVE', 'Activée après validation'
        REJECTED = 'REJECTED', 'Refusée'
        EXPIRED = 'EXPIRED', 'Expirée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.OneToOneField('environments.Environment', on_delete=models.PROTECT, related_name='ha_option')
    desired_replicas = models.PositiveSmallIntegerField(default=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey('administrators.Administrator', on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='approved_ha_options')
    sandbox_checkout = models.ForeignKey('SandboxCheckout', on_delete=models.SET_NULL, null=True, blank=True, related_name='ha_options')
    billing_reference = models.CharField(max_length=120, blank=True,
                                         help_text='Référence de facturation vérifiée hors API client.')
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if not 2 <= self.desired_replicas <= 20:
            raise ValidationError({'desired_replicas': 'Entre 2 et 20 réplicas.'})
        if self.status == self.Status.ACTIVE:
            if self.sandbox_checkout_id:
                checkout = self.sandbox_checkout
                if (checkout.status != SandboxCheckout.Status.ACCEPTED or
                        checkout.environment_id != self.environment_id or
                        checkout.resource_plan.max_instances < 2 or
                        checkout.expires_at <= timezone.now() or
                        self.billing_reference != checkout.reference or not self.activated_at):
                    raise ValidationError('La HA de test exige un reçu simulé actif lié au même environnement.')
            elif not self.approved_by_id or not self.billing_reference or not self.activated_at:
                raise ValidationError('La HA exige une approbation admin et une facturation vérifiée.')

    class Meta:
        db_table = 'plans_highavailabilityoption'


class SandboxCheckout(models.Model):
    """Preuve de paiement SIMULÉ : jamais une transaction financière réelle."""
    class Status(models.TextChoices):
        ACCEPTED = 'ACCEPTED', 'Simulation acceptée'
        REVOKED = 'REVOKED', 'Simulation révoquée'

    class Period(models.TextChoices):
        MONTHLY = 'monthly', 'Mensuel'
        ANNUAL = 'annual', 'Annuel'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='sandbox_checkouts')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='sandbox_checkouts')
    resource_plan = models.ForeignKey('plan_catalog.ResourcePlan', on_delete=models.PROTECT, related_name='sandbox_checkouts')
    plan_code = models.CharField(max_length=16)
    period = models.CharField(max_length=12, choices=Period.choices)
    amount_ariary = models.PositiveIntegerField()
    reference = models.CharField(max_length=120, unique=True)
    idempotency_key = models.UUIDField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACCEPTED)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'plans_sandboxcheckout'
        constraints = [models.UniqueConstraint(fields=['environment', 'created_by', 'idempotency_key'], name='unique_sandbox_checkout_request')]

    def __str__(self):
        return f'TEST {self.plan_code} {self.environment_id} {self.reference}'
