"""Account-wide subscription for the temporary no-charge checkout flow.

Only the verified account owner may subscribe. The server remains authoritative;
projects cannot upgrade themselves by editing a frontend form. No payment
credentials or externally verifiable payment claims are created.
"""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.plans.entitlements.models import (AccountSubscription, ApplicationEntitlement,
                                             HighAvailabilityOption, SandboxCheckout)
from apps.plans.entitlements.sandbox import TEST_PLANS, enabled_for


def active_subscription(user):
    if not enabled_for(user):
        return None
    subscription = AccountSubscription.objects.select_related('receipt', 'receipt__resource_plan').filter(user=user).first()
    if not subscription:
        return None
    receipt = subscription.receipt
    return receipt if (receipt.status == SandboxCheckout.Status.ACCEPTED and
                       receipt.expires_at > timezone.now()) else None


def account_status(user):
    receipt = active_subscription(user)
    if not receipt:
        return {'plan': 'free', 'plan_name': 'Découverte', 'max_instances': 1,
                'ha_available': False, 'active': False, 'period': None, 'expires_at': None,
                'can_subscribe': enabled_for(user), 'receipt': None}
    row = TEST_PLANS.get(receipt.plan_code)
    if not row:
        return {'plan': 'free', 'plan_name': 'Découverte', 'max_instances': 1,
                'ha_available': False, 'active': False, 'period': None, 'expires_at': None,
                'can_subscribe': enabled_for(user), 'receipt': None}
    return {'plan': receipt.plan_code, 'plan_name': 'Professionnel' if receipt.plan_code == 'pro' else 'Essentiel',
            'max_instances': row['instances'], 'ha_available': receipt.plan_code == 'pro',
            'active': True, 'period': receipt.period, 'expires_at': receipt.expires_at.isoformat(),
            'can_subscribe': enabled_for(user),
            'receipt': {'reference': receipt.reference, 'amount_display_ariary': receipt.amount_ariary,
                        'amount_charged_ariary': 0, 'created_at': receipt.created_at.isoformat()}}


def _plan(plan_code):
    row = TEST_PLANS[plan_code]
    plan, _ = ResourcePlan.objects.get_or_create(code=row['code'], defaults={
        'name': 'Professionnel' if plan_code == 'pro' else 'Essentiel',
        'description': 'Formule de démonstration, aucun débit financier.',
        'cpu_millicores': row['cpu'], 'memory_mb': row['ram'], 'storage_mb': row['storage'],
        'max_instances': row['instances'], 'is_premium': True, 'is_default': False,
        'is_active': True, 'display_order': 80 if plan_code == 'essential' else 90,
    })
    if (not plan.is_active or not plan.is_premium or plan.max_instances != row['instances'] or
            plan.cpu_millicores != row['cpu'] or plan.memory_mb != row['ram']):
        raise ValidationError('Configuration du catalogue incohérente : administrateur requis.')
    return plan


def grant_owned_environment(env, receipt):
    """Never override manually approved/paid rights; called inside a DB transaction."""
    from apps.plans.entitlements.models import AccountSubscription
    if (receipt.environment_id is not None or
            env.project.organization.owner_id != receipt.created_by_id or
            not AccountSubscription.objects.filter(user_id=receipt.created_by_id, receipt_id=receipt.id).exists()):
        raise PermissionDenied('Abonnement non applicable à cet environnement.')
    ent = ApplicationEntitlement.objects.select_for_update().filter(environment=env).first()
    if ent is None:
        raise ValidationError('Droit initial de cet environnement absent.')
    if ent.grant_source not in (ApplicationEntitlement.GrantSource.SYSTEM, ApplicationEntitlement.GrantSource.TEST):
        return False
    offer = DeploymentModeOffer.objects.filter(
        code='DEVSECOPS' if receipt.plan_code == 'pro' else 'DEVOPS', is_active=True).first()
    if not offer:
        raise ValidationError('Catalogue DevOps non initialisé.')
    ent.mode_offer = offer
    ent.resource_plan = receipt.resource_plan
    ent.status = ApplicationEntitlement.Status.ACTIVE
    ent.grant_source = ApplicationEntitlement.GrantSource.TEST
    ent.sandbox_checkout = receipt
    ent.approved_by = None
    ent.activated_at = timezone.now()
    ent.expires_at = receipt.expires_at
    ent.full_clean()
    ent.save()
    option = HighAvailabilityOption.objects.select_for_update().filter(environment=env).first()
    if receipt.plan_code == 'pro':
        if option and option.status == HighAvailabilityOption.Status.ACTIVE and not option.sandbox_checkout_id:
            return True  # Existing admin option remains authoritative.
        if option is None:
            option = HighAvailabilityOption(environment=env)
        option.desired_replicas = 2
        option.status = HighAvailabilityOption.Status.ACTIVE
        option.approved_by = None
        option.sandbox_checkout = receipt
        option.billing_reference = receipt.reference
        option.activated_at = timezone.now()
        option.expires_at = receipt.expires_at
        option.full_clean()
        option.save()
    elif option and option.sandbox_checkout_id:
        option.status = HighAvailabilityOption.Status.REJECTED
        option.save(update_fields=['status', 'updated_at'])
    return True


def checkout_account(user, plan_code, period, idempotency_key):
    if not enabled_for(user):
        raise PermissionDenied('Souscription indisponible pour ce compte.')
    if plan_code not in TEST_PLANS or period not in ('monthly', 'annual'):
        raise ValidationError('Offre ou périodicité invalide.')
    try:
        key = uuid.UUID(str(idempotency_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({'idempotency_key': 'Identifiant de requête invalide.'})
    row = TEST_PLANS[plan_code]
    with transaction.atomic():
        # Lock the account so concurrent checkout requests serialize reliably.
        from apps.accounts.user.models import User
        User.objects.select_for_update().get(pk=user.pk)
        previous = SandboxCheckout.objects.filter(created_by=user, environment__isnull=True,
                                                  idempotency_key=key).first()
        if previous:
            if previous.plan_code != plan_code or previous.period != period:
                raise ValidationError('Une requête identique ne peut changer de formule.')
            if previous.status != SandboxCheckout.Status.ACCEPTED or previous.expires_at <= timezone.now():
                raise ValidationError('Ancienne demande de souscription expirée ou révoquée.')
            if not AccountSubscription.objects.filter(user=user, receipt=previous).exists():
                raise ValidationError('Ancienne transaction remplacée ; utilisez une nouvelle demande.')
            return previous
        plan = _plan(plan_code)
        now = timezone.now()
        receipt = SandboxCheckout.objects.create(
            environment=None, created_by=user, resource_plan=plan,
            plan_code=plan_code, period=period, amount_ariary=row[period],
            reference='AKDEMO-' + uuid.uuid4().hex.upper(), idempotency_key=key,
            expires_at=now + timedelta(days=365 if period == 'annual' else 30),
            status=SandboxCheckout.Status.ACCEPTED,
        )
        AccountSubscription.objects.update_or_create(user=user, defaults={'receipt': receipt})
        from apps.projects.environments.models import Environment
        for env in Environment.objects.filter(project__organization__owner=user, project__status='ACTIVE',
                                              is_active=True).select_related('project__organization'):
            grant_owned_environment(env, receipt)
        return receipt
