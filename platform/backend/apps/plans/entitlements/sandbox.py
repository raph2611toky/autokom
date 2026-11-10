"""AUTOKOM abonnement simulé, sans appel à un prestataire financier.

Ceci n'est pas un système de paiement de production. L'accès doit rester limité
aux adresses de test autorisées par configuration serveur.
"""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.organizations.models import OrganizationMembership
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption, SandboxCheckout

# Tarifs de démonstration en ariary ; jamais débités.
TEST_PLANS = {
    'essential': {'code': 'autokom-essential-test', 'name': 'Essentiel (TEST)', 'cpu': 500, 'ram': 768,
                  'storage': 2048, 'instances': 1, 'monthly': 49000, 'annual': 490000},
    'pro': {'code': 'autokom-pro-test', 'name': 'Professionnel (TEST)', 'cpu': 750, 'ram': 1024,
            'storage': 4096, 'instances': 2, 'monthly': 149000, 'annual': 1490000},
}


def enabled_for(user):
    return bool(
        settings.AUTOKOM_BILLING_SANDBOX_ENABLED and
        settings.AUTOKOM_BILLING_SANDBOX_TEST_EMAILS and
        user is not None and getattr(user, 'is_active', False) and
        getattr(user, 'is_email_verified', False) and
        getattr(user, 'email', '').strip().casefold() in settings.AUTOKOM_BILLING_SANDBOX_TEST_EMAILS
    )


def require_billing_owner(user, project):
    if project.organization.owner_id == user.pk:
        return
    if OrganizationMembership.objects.filter(
            organization=project.organization, user=user, is_active=True,
            role=OrganizationMembership.Role.ADMIN).exists():
        return
    raise PermissionDenied('Seul le propriétaire ou un administrateur de l’organisation peut souscrire.')


def checkout(user, project, env, plan_code, period, request_id):
    if not enabled_for(user):
        raise PermissionDenied('Paiement simulé désactivé pour ce compte.')
    require_billing_owner(user, project)
    if not isinstance(plan_code, str) or plan_code not in TEST_PLANS:
        raise ValidationError({'plan': 'Choisissez Essentiel ou Professionnel.'})
    if not isinstance(period, str) or period not in ('monthly', 'annual'):
        raise ValidationError({'period': 'Période mensuelle ou annuelle attendue.'})
    try:
        if not isinstance(request_id, str) or len(request_id) > 64:
            raise ValueError('Invalid checkout key')
        request_id = uuid.UUID(request_id)
    except (TypeError, ValueError, AttributeError):
        raise ValidationError({'idempotency_key': 'Identifiant de paiement de test UUID requis.'})
    row = TEST_PLANS[plan_code]
    now = timezone.now()
    expiry = now + timedelta(days=365 if period == 'annual' else 30)
    with transaction.atomic():
        # Verrouille le droit existant : deux POST concurrents ne doivent jamais
        # attribuer deux plans différents sans passage par la transaction DB.
        entitlement = ApplicationEntitlement.objects.select_for_update().filter(environment=env).first()
        if entitlement is None:
            raise ValidationError('Droit initial de l’environnement absent.')
        previous = SandboxCheckout.objects.filter(environment=env, created_by=user, idempotency_key=request_id).first()
        if previous:
            if previous.plan_code != plan_code or previous.period != period:
                raise ValidationError({'idempotency_key': 'Une même requête ne peut changer de formule.'})
            if previous.status != SandboxCheckout.Status.ACCEPTED or previous.expires_at <= now:
                raise ValidationError({'idempotency_key': 'Ce reçu de test a expiré ou a été révoqué.'})
            if entitlement.sandbox_checkout_id != previous.id or entitlement.grant_source != ApplicationEntitlement.GrantSource.TEST:
                raise ValidationError({'idempotency_key': 'Cette ancienne transaction n’est plus le droit courant.'})
            return previous
        if entitlement.status == ApplicationEntitlement.Status.ACTIVE and entitlement.grant_source not in (
                ApplicationEntitlement.GrantSource.SYSTEM, ApplicationEntitlement.GrantSource.TEST):
            raise PermissionDenied('Un abonnement réel administré ne peut être remplacé par une simulation.')
        existing_ha = HighAvailabilityOption.objects.select_for_update().filter(environment=env).first()
        if existing_ha and existing_ha.status == HighAvailabilityOption.Status.ACTIVE and not existing_ha.sandbox_checkout_id:
            raise PermissionDenied('Une option HA administrée ne peut pas être remplacée par un abonnement simulé.')
        if plan_code == 'essential' and existing_ha and existing_ha.status == HighAvailabilityOption.Status.ACTIVE:
            raise ValidationError({'plan': 'Désactivez d’abord la haute disponibilité avant de changer de formule.'})
        offer_code = 'DEVSECOPS' if plan_code == 'pro' else 'DEVOPS'
        offer = DeploymentModeOffer.objects.filter(code=offer_code, is_active=True).first()
        if offer is None:
            raise ValidationError('Catalogue DevOps non initialisé.')
        plan, _ = ResourcePlan.objects.get_or_create(code=row['code'], defaults={
            'name': row['name'], 'description': 'Simulation de facturation AUTOKOM — aucun paiement réel.',
            'cpu_millicores': row['cpu'], 'memory_mb': row['ram'], 'storage_mb': row['storage'],
            'max_instances': row['instances'], 'is_premium': True,
            'is_default': False, 'is_active': True, 'display_order': 80 if plan_code == 'essential' else 90,
        })
        if (not plan.is_active or not plan.is_premium or plan.max_instances != row['instances'] or
                plan.cpu_millicores != row['cpu'] or plan.memory_mb != row['ram']):
            raise ValidationError('Le plan de démonstration a été modifié dans le catalogue : contrôle administrateur requis.')
        receipt = SandboxCheckout.objects.create(
            environment=env, created_by=user, resource_plan=plan,
            plan_code=plan_code, period=period, amount_ariary=row[period],
            reference='AKTEST-' + uuid.uuid4().hex.upper(), idempotency_key=request_id,
            expires_at=expiry, status=SandboxCheckout.Status.ACCEPTED,
        )
        entitlement.resource_plan = plan
        entitlement.mode_offer = offer
        entitlement.status = ApplicationEntitlement.Status.ACTIVE
        entitlement.grant_source = ApplicationEntitlement.GrantSource.TEST
        entitlement.sandbox_checkout = receipt
        entitlement.approved_by = None
        entitlement.activated_at = now
        entitlement.expires_at = expiry
        entitlement.full_clean()
        entitlement.save()
        if plan_code == 'pro':
            option, _ = HighAvailabilityOption.objects.select_for_update().get_or_create(environment=env)
            option.desired_replicas = 2
            option.status = HighAvailabilityOption.Status.ACTIVE
            option.approved_by = None
            option.sandbox_checkout = receipt
            option.billing_reference = receipt.reference
            option.activated_at = now
            option.expires_at = expiry
            option.full_clean()
            option.save()
        elif existing_ha and existing_ha.sandbox_checkout_id:
            existing_ha.status = HighAvailabilityOption.Status.REJECTED
            existing_ha.full_clean()
            existing_ha.save(update_fields=['status', 'updated_at'])
        return receipt


def receipt_summary(receipt):
    valid = bool(settings.AUTOKOM_BILLING_SANDBOX_ENABLED and receipt.status == SandboxCheckout.Status.ACCEPTED and receipt.expires_at > timezone.now())
    return {
        'id': str(receipt.pk), 'reference': receipt.reference,
        'plan': receipt.plan_code, 'period': receipt.period,
        'amount_display_ariary': receipt.amount_ariary,
        'amount_charged_ariary': 0, 'mode': 'SANDBOX',
        'status': receipt.status, 'is_valid': valid, 'expires_at': receipt.expires_at.isoformat(),
        'max_instances': receipt.resource_plan.max_instances,
        'ha_option_active': receipt.plan_code == 'pro' and valid,
    }
