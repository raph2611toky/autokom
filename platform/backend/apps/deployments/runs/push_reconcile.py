"""Recover lost/deferred push events without a second CI path.

The ordinary GitHub App webhook stays primary. A bounded, low-frequency
GitHub branch HEAD check compensates for missing deliveries, with the same
repository authorization, entitlement and Jenkins checks as manual deployment.
No daemon/thread, new credentials, DB schema change or client socket needed.
"""
import logging
from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError
from apps.projects.git.models import GitWebhookEvent
from apps.deployments.runs.models import DeploymentRun

logger = logging.getLogger('autokom.deployments')


def reconcile_received_pushes(limit=12):
    """Retry signed push events left pending when an older run was active."""
    from apps.deployments.runs.services import queue_verified_push
    events = list(GitWebhookEvent.objects.filter(
        status=GitWebhookEvent.Status.RECEIVED,
        signature_verified=True, event_type='push',
    ).order_by('received_at').values_list('pk', flat=True)[:limit])
    updated = 0
    for event_id in events:
        try:
            updated += queue_verified_push(GitWebhookEvent(pk=event_id))
        except Exception as exc:
            # Never log signed webhook bodies, OAuth tokens or request headers.
            logger.warning('GitHub push pending: dispatch could not be retried (%s)', type(exc).__name__)
    return updated


def needs_branch_deployment(current_sha, last_sha):
    """A commit is attempted at most once automatically for an environment."""
    return bool(current_sha) and current_sha.lower() != (last_sha or '').lower()


def reconcile_branch_heads(*, offset=0, limit=5, repository=None):
    """Safe fallback when GitHub never delivered the webhook.

    Check at most five enabled environments every ~120 seconds, cycling
    through the list across ticks. Only remote branches authorized by the
    project owner's existing GitHub App session may trigger a run.
    """
    from apps.projects.environments.models import Environment
    from apps.projects.project.models import Project
    from apps.deployments.runs.services import (
        _create_run, _select_branch_commit, plan_deployment, ensure_runner_ready,
    )
    from apps.projects.configurations.models import DeploymentConfig

    eligible_qs = Environment.objects.filter(
        is_active=True, project__status=Project.Status.ACTIVE,
        project__git_connection__is_active=True,
        project__git_connection__installation_id__isnull=False,
        frontend_stacks__isnull=True, backend_stacks__isnull=True,
    ).order_by('pk')
    if repository:
        eligible_qs = eligible_qs.filter(project__repository_name__iexact=repository)
    count = eligible_qs.count()
    if not count:
        return {'created': 0, 'examined': 0, 'total': 0, 'next_offset': 0}
    start = offset % count
    ids = list(eligible_qs.values_list('pk', flat=True)[start:start + limit])
    if len(ids) < limit:
        ids += list(eligible_qs.values_list('pk', flat=True)[:min(limit - len(ids), count - len(ids))])
    created = 0
    examined = 0
    for env in Environment.objects.filter(pk__in=ids).select_related('project', 'project__created_by', 'project__git_connection'):
        cfg = DeploymentConfig.objects.filter(environment=env, is_draft=False).order_by('-version').first()
        if not cfg or not cfg.auto_deploy or (cfg.auto_deploy_branch or cfg.branch) != cfg.branch:
            continue
        examined += 1
        if DeploymentRun.objects.filter(environment=env, status__in=['QUEUED', 'RUNNING']).exists():
            continue
        try:
            sha = _select_branch_commit(env.project.created_by, cfg)
        except (APIException, ValidationError) as exc:
            logger.warning('GitHub branch reconciliation: source access unavailable for project %s (%s)',
                           env.project_id, type(exc).__name__)
            continue
        latest = DeploymentRun.objects.filter(environment=env).exclude(trigger_type='ROLLBACK').order_by('-created_at').first()
        if not needs_branch_deployment(sha, latest.commit_sha if latest else ''):
            continue
        try:
            current_cfg, resources = plan_deployment(env)
            ensure_runner_ready()
        except (APIException, ValidationError) as exc:
            logger.warning('GitHub branch reconciliation: deployment preflight rejected for project %s (%s)',
                           env.project_id, type(exc).__name__)
            continue
        idem = f'github-head:{env.pk}:{sha.lower()}'
        with transaction.atomic():
            locked = type(env).objects.select_for_update().get(pk=env.pk)
            if locked.frontend_stacks.exists() or locked.backend_stacks.exists():
                continue
            if DeploymentRun.objects.filter(environment=locked, status__in=['QUEUED', 'RUNNING']).exists():
                continue
            if DeploymentRun.objects.filter(idempotency_key=idem).exists():
                continue
            last_now = DeploymentRun.objects.filter(environment=locked).exclude(trigger_type='ROLLBACK').order_by('-created_at').first()
            if last_now and last_now.commit_sha.lower() == sha.lower():
                continue
            try:
                actual_cfg, actual_resources = plan_deployment(locked)
            except (APIException, ValidationError):
                continue
            if (actual_cfg.pk != current_cfg.pk or not actual_cfg.auto_deploy or
                    actual_resources != resources):
                continue
            _create_run(locked, actual_cfg, actual_resources, actor=None,
                        trigger=DeploymentRun.Trigger.GIT_PUSH,
                        sha=sha.lower(), idem=idem)
            created += 1
            logger.info('AUTOKOM GitHub HEAD change: auto deploy queued for project %s', locked.project_id)
    return {'created': created, 'examined': examined, 'total': count,
            'next_offset': (start + len(ids)) % count}
