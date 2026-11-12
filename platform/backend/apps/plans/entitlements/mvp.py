"""MVP authorization and account-wide GitHub repository lock.

The project and deployment-run history are protected, so deleting a project never
resets the lock. This guard runs for both manual and webhook/stack pipelines via
check_entitlement. The UI is not an authorization boundary.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone as datetime_timezone
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.plans.entitlements.mvp_policy import entitlement_window

MVP_CODE = 'mvp-lancement'
MVP_HA_REF = 'AUTOKOM-MVP-LANCEMENT'
LOCK_STATUSES = ('QUEUED', 'RUNNING', 'PREPARED', 'SUCCEEDED')


def enabled():
    return os.environ.get('AUTOKOM_MVP_ENABLED', '').strip().lower() in ('1', 'true', 'yes')


def unlimited_user(user):
    # Operator-managed immutable UUID allowlist, never accepted from client data.
    values = os.environ.get('AUTOKOM_MVP_UNLIMITED_USER_IDS', '')
    allowed = {x.strip().lower() for x in values.split(',') if x.strip()}
    return str(user.pk).lower() in allowed


def _repo_history(project):
    from apps.deployments.runs.models import DeploymentRun
    from apps.projects.git.models import GitConnection
    connection = project.git_connection
    if not connection or not connection.external_account_id:
        raise PermissionDenied('Compte GitHub non relié au projet.')
    # Serialize decisions for the GitHub account during queue operations, including
    # multiple AUTOKOM organizations using the same GitHub installation.
    if transaction.get_connection().in_atomic_block:
        list(GitConnection.objects.select_for_update().filter(
            provider=connection.provider, external_account_id=connection.external_account_id
        ).order_by('pk').values_list('pk', flat=True))
    return DeploymentRun.objects.filter(
        environment__project__git_connection__provider=connection.provider,
        environment__project__git_connection__external_account_id=connection.external_account_id,
        status__in=LOCK_STATUSES,
    ).select_related('environment__project').order_by('created_at', 'pk')


def enforce_repository_lock(project, *, actor=None):
    if not enabled() or (actor is not None and unlimited_user(actor)):
        return
    target = str(project.repository_external_id)
    for deployment in _repo_history(project):
        existing = deployment.environment.project
        if str(existing.repository_external_id) != target:
            raise PermissionDenied(
                'Ce compte GitHub a déjà publié un autre dépôt. Le retirer ou '
                'supprimer son projet ne libère pas ce droit de lancement.'
            )


def _first_live(project):
    from apps.deployments.runs.models import DeploymentRun
    run = DeploymentRun.objects.filter(
        environment__project=project, status=DeploymentRun.Status.SUCCEEDED,
    ).order_by('finished_at', 'created_at', 'pk').first()
    first = (run.finished_at or run.created_at) if run else None
    # Existing deployments may be grandfathered from the official launch
    # instant. Future projects always start from their first successful run.
    raw = os.environ.get('AUTOKOM_MVP_ROLLOUT_AT', '').strip()
    if first is not None and raw:
        try:
            rollout = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if rollout.tzinfo is None:
                raise ValueError('timezone missing')
        except ValueError as exc:
            raise RuntimeError('AUTOKOM_MVP_ROLLOUT_AT must be ISO 8601 with timezone') from exc
        first = max(first, rollout.astimezone(datetime_timezone.utc))
    return first


def _stack_has_backend(project):
    from apps.projects.configurations.models import ServiceStack
    return ServiceStack.objects.filter(project=project).exists()


def window_for_project(project, *, cfg=None, now=None):
    from apps.projects.configurations.models import DeploymentConfig, ServiceStack
    now = now or timezone.now()
    owner = project.organization.owner
    unlimited = unlimited_user(owner)
    # A backend-only service also consumes the shorter server-side allowance.
    from apps.projects.configurations.services import BACKEND_RUNTIMES
    backend = (_stack_has_backend(project) or
               DeploymentConfig.objects.filter(
                   environment__project=project, is_draft=False,
                   settings__frontend_profile__in=list(BACKEND_RUNTIMES),
               ).exists())
    ha = bool(getattr(cfg, 'high_availability_requested', False))
    if not ha:
        ha = ServiceStack.objects.filter(project=project, high_availability_requested=True).exists()
    if not ha:
        ha = DeploymentConfig.objects.filter(
            environment__project=project, is_draft=False,
            high_availability_requested=True).exists()
    return entitlement_window(_first_live(project), has_backend=backend,
                              high_availability=ha, unlimited=unlimited, now=now)


def _launch_plan():
    from apps.plans.catalog.models import ResourcePlan
    plan, _ = ResourcePlan.objects.get_or_create(
        code=MVP_CODE,
        defaults={'name': 'Offre gratuite de lancement', 'description':
                  'Accès temporaire sans paiement, soumis aux limites de durée.',
                  'cpu_millicores': 750, 'memory_mb': 1024, 'storage_mb': 2048,
                  'max_instances': 2, 'is_premium': False, 'is_default': False,
                  'is_active': True, 'display_order': 10})
    if not (plan.is_active and not plan.is_premium and plan.max_instances == 2
            and plan.cpu_millicores == 750 and plan.memory_mb == 1024
            and plan.storage_mb == 2048):
        raise PermissionDenied('La configuration de l’offre de lancement a été modifiée : contacter le support.')
    return plan


@transaction.atomic
def authorize(cfg):
    """MVP feature gate; no payment is implied and no user can forge a plan."""
    if not enabled():
        raise RuntimeError('MVP access requested while disabled')
    from apps.plans.catalog.models import DeploymentModeOffer
    from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption
    env, project = cfg.environment, cfg.environment.project
    owner = project.organization.owner
    if project.status != 'ACTIVE' or not env.is_active:
        raise PermissionDenied('Environnement désactivé.')
    enforce_repository_lock(project, actor=owner)
    window = window_for_project(project, cfg=cfg)
    if window['state'] in ('grace', 'purge_due'):
        raise PermissionDenied('Offre de lancement expirée : publication suspendue.')
    plan = _launch_plan()
    ent = ApplicationEntitlement.objects.select_for_update().filter(environment=env).first() \
          if transaction.get_connection().in_atomic_block else ApplicationEntitlement.objects.filter(environment=env).first()
    if ent is None:
        raise PermissionDenied('Droit initial manquant : demander une réparation administrateur.')
    # Never overwrite a manually awarded entitlement or real financial grant.
    if ent.grant_source not in (ApplicationEntitlement.GrantSource.SYSTEM,
                                ApplicationEntitlement.GrantSource.TEST):
        if ent.status != ApplicationEntitlement.Status.ACTIVE:
            raise PermissionDenied('Offre désactivée.')
        return ent.resource_plan
    mode = DeploymentModeOffer.objects.filter(code='DEVOPS', is_active=True).first()
    if mode is None:
        raise PermissionDenied('Catalogue DevOps indisponible.')
    changed = (ent.resource_plan_id != plan.pk or ent.mode_offer_id != mode.pk or
               ent.status != ApplicationEntitlement.Status.ACTIVE or
               ent.grant_source != ApplicationEntitlement.GrantSource.SYSTEM or
               ent.sandbox_checkout_id is not None or ent.expires_at != window['expires_at'])
    if changed:
        ent.resource_plan = plan
        ent.mode_offer = mode  # selected DevSecOps mode authorized by this policy
        ent.status = ApplicationEntitlement.Status.ACTIVE
        ent.grant_source = ApplicationEntitlement.GrantSource.SYSTEM
        ent.sandbox_checkout = None
        ent.activated_at = ent.activated_at or timezone.now()
        ent.expires_at = window['expires_at']
        ent.full_clean()
        ent.save()
    # The old deployment UI may submit the paid-plan UI code 'pro' to enable
    # its HA toggle. NEVER grant those resources: the returned plan is always
    # the bounded launch plan, used for the actual deployment snapshots.
    if cfg.high_availability_requested:
        opt, _ = HighAvailabilityOption.objects.get_or_create(environment=env)
        if opt.approved_by_id and not opt.sandbox_checkout_id:
            return plan  # separately approved admin entitlement retained
        opt.desired_replicas = 2
        opt.status = HighAvailabilityOption.Status.ACTIVE
        opt.approved_by = None
        opt.sandbox_checkout = None
        opt.billing_reference = MVP_HA_REF
        opt.activated_at = opt.activated_at or timezone.now()
        opt.expires_at = window['expires_at']
        # No financial transaction is asserted. The model validates this special grant.
        opt.full_clean()
        opt.save()
    return plan


def status_for_account(user):
    from apps.projects.project.models import Project
    if not enabled():
        return None
    projects = Project.objects.filter(organization__owner=user).select_related('organization__owner')
    records = []
    for project in projects:
        if not project.repository_external_id:
            continue
        win = window_for_project(project)
        records.append({'project_id': str(project.pk), 'repository': project.repository_name,
                        'state': win['state'], 'days': win['days'],
                        'expires_at': win['expires_at'].isoformat() if win['expires_at'] else None,
                        'grace_ends_at': win['purge_after'].isoformat() if win['purge_after'] else None,
                        'remaining_seconds': win['remaining_seconds']})
    return {'offer': 'Offre gratuite de lancement', 'payment_available': False,
            'devops': True, 'devsecops': True, 'high_availability': True,
            'unlimited': unlimited_user(user), 'projects': records}
