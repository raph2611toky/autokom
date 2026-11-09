"""Durable two-candidate orchestration; never alter legacy single-service runs."""
import logging
import re
import secrets
from django.db import transaction
from django.utils import timezone
from apps.deployments.runs.models import DeploymentRun, StackDeployment, PipelineStageRun
from apps.projects.configurations.models import DeploymentConfig, ServiceStack

logger = logging.getLogger('autokom.stack')
TERMINAL = {'FAILED', 'BLOCKED', 'CANCELLED'}
OPEN = {'QUEUED', 'PREPARING', 'ACTIVATING'}


def stack_execution_for_run(run_id):
    group = StackDeployment.objects.filter(backend_run_id=run_id).select_related('stack').first()
    if group:
        return group, 'backend'
    group = StackDeployment.objects.filter(frontend_run_id=run_id).select_related('stack').first()
    return (group, 'frontend') if group else (None, None)


def _rollback_pair(group):
    from apps.deployments.runs.autokom_socket import request_control
    from apps.deployments.domains.models import Domain
    ok = True
    for run in (group.frontend_run, group.backend_run):
        if run is None:
            continue
        try:
            result = request_control('runtime.rollback', {'run_id': str(run.id),
                'environment_id': str(run.environment_id)}, timeout=35)
            if result.get('rolled_back') and not result.get('previous_available'):
                Domain.objects.filter(environment_id=run.environment_id, domain_type='SUBDOMAIN').update(
                    status='PENDING', tls_status='', is_primary=False)
        except Exception as exc:
            ok = False
            logger.error('Rollback de groupe non confirmé, run=%s (%s)', run.pk, type(exc).__name__)
    return ok


def _fail_group(group, *, code):
    rolled_back = _rollback_pair(group)
    group.status = 'FAILED' if rolled_back else 'BLOCKED'
    group.error_code = code
    group.finished_at = timezone.now() if rolled_back else None
    group.save(update_fields=['status', 'error_code', 'finished_at'])
    for run in (group.frontend_run, group.backend_run):
        if run and run.status == 'PREPARED':
            DeploymentRun.objects.filter(pk=run.pk).update(
                status='FAILED' if rolled_back else 'BLOCKED', finished_at=timezone.now() if rolled_back else None,
                error_message='La publication groupée a échoué ; les anciens services sont conservés.' if rolled_back
                              else 'Retour arrière non confirmé : intervention administrative nécessaire.')
            PipelineStageRun.objects.filter(deployment=run,stage_key__in=('release','health')).update(
                status='FAILED' if rolled_back else 'BLOCKED', finished_at=timezone.now())
    return True


def _new_frontend_run(group):
    from apps.deployments.runs.services import _create_run, DeploymentConflict
    with transaction.atomic():
        group = StackDeployment.objects.select_for_update().select_related(
            'stack__frontend_environment','frontend_configuration').get(pk=group.pk)
        if group.frontend_run_id:
            return False
        cfg = group.frontend_configuration
        if cfg.environment_id != group.stack.frontend_environment_id or cfg.is_draft:
            raise DeploymentConflict('Configuration frontend du groupe invalide.')
        run = _create_run(cfg.environment, cfg, group.frontend_resources,
            actor=group.backend_run.requested_by, trigger=group.backend_run.trigger_type,
            sha=group.frontend_sha, idem=group.idempotency_key + ':frontend',
            event=group.backend_run.webhook_event)
        group.frontend_run = run
        group.status = 'PREPARING'
        group.save(update_fields=['frontend_run', 'status'])
        return True


def _activate_group(group):
    """Domains only become public when BOTH isolated candidates are healthy."""
    from apps.deployments.domains.services import activate_managed_domains_for_release
    from apps.deployments.runs.autokom_socket import request_control
    from apps.deployments.runs.worker import register_release, add_log
    group.status = 'ACTIVATING'
    group.save(update_fields=['status'])
    try:
        # The backend must have its own public HTTPS name first.
        activate_managed_domains_for_release(group.stack.backend_environment)
        activate_managed_domains_for_release(group.stack.frontend_environment)
    except Exception as exc:
        logger.warning('Refus HTTPS groupé pour %s (%s)', group.pk, type(exc).__name__)
        return _fail_group(group, code='AUTOKOM_STACK_HTTPS_FAILED')
    try:
        request_control('runtime.commit_pair', {'services': [
            {'run_id': str(group.backend_run_id), 'environment_id': str(group.stack.backend_environment_id)},
            {'run_id': str(group.frontend_run_id), 'environment_id': str(group.stack.frontend_environment_id)},
        ]}, timeout=90)
    except Exception as exc:
        # Cannot safely roll back a partially committed pair: retain both
        # candidates and require reconciliation, rather than pretending success.
        group.status = 'BLOCKED'
        group.error_code = 'AUTOKOM_STACK_COMMIT_RECONCILE_REQUIRED'
        group.save(update_fields=['status', 'error_code'])
        logger.error('Commit stack à réconcilier %s (%s)', group.pk, type(exc).__name__)
        return True
    try:
        for run in (group.backend_run, group.frontend_run):
            register_release(run)
            for key in ('release','health'):
                PipelineStageRun.objects.filter(deployment=run,stage_key=key).update(
                    status='SUCCESS', finished_at=timezone.now())
            DeploymentRun.objects.filter(pk=run.pk, status='PREPARED').update(
                status='SUCCEEDED', finished_at=timezone.now())
            add_log(run, 'AUTOKOM_STAGE:release:SUCCESS')
            add_log(run, 'AUTOKOM_STAGE:health:SUCCESS')
            add_log(run, 'AUTOKOM_LOG: Les deux services et domaines HTTPS sont en ligne.')
    except Exception as exc:
        group.status = 'BLOCKED'
        group.error_code = 'AUTOKOM_STACK_DATABASE_RECONCILE_REQUIRED'
        group.save(update_fields=['status', 'error_code'])
        logger.error('Synchronisation SQL stack à réconcilier %s (%s)', group.pk, type(exc).__name__)
        return True
    group.status = 'SUCCEEDED'
    group.error_code = ''
    group.finished_at = timezone.now()
    group.save(update_fields=['status', 'error_code', 'finished_at'])
    return True


def reconcile_stack_deployments(limit=4):
    changed = False
    for group in (StackDeployment.objects.select_related('stack__frontend_environment',
                  'stack__backend_environment', 'backend_run', 'frontend_run', 'frontend_configuration')
                  .filter(status__in=OPEN).order_by('created_at')[:limit]):
        back = group.backend_run.status
        front = group.frontend_run.status if group.frontend_run else None
        if back in TERMINAL or front in TERMINAL:
            changed = _fail_group(group, code='AUTOKOM_STACK_BUILD_FAILED') or changed
        elif back == 'PREPARED' and group.frontend_run_id is None:
            changed = _new_frontend_run(group) or changed
        elif back == 'PREPARED' and front == 'PREPARED':
            changed = _activate_group(group) or changed
        elif group.status == 'QUEUED' and back in {'QUEUED','RUNNING'}:
            group.status = 'PREPARING'
            group.save(update_fields=['status'])
            changed = True
    return changed


def reconcile_stack_pushes(limit=3):
    """GitHub calls happen outside the webhook DB transaction and off the CI path."""
    from apps.projects.git.models import GitWebhookEvent
    from apps.projects.configurations.stack_services import launch_stack
    from apps.deployments.runs.services import _select_branch_commit
    from apps.projects.configurations.models import DeploymentConfig
    candidates = GitWebhookEvent.objects.filter(
        event_type='push', signature_verified=True, status=GitWebhookEvent.Status.RECEIVED,
    ).order_by('received_at')[:limit]
    count = 0
    for event in candidates:
        stacks = ServiceStack.objects.filter(project__git_connection=event.connection,
            project__repository_external_id=event.repository_external_id,
            project__status='ACTIVE', auto_deploy=True).select_related('project')
        for stack in stacks:
            front_cfg = DeploymentConfig.objects.filter(environment=stack.frontend_environment,
                is_draft=False).order_by('-version').first()
            back_cfg = DeploymentConfig.objects.filter(environment=stack.backend_environment,
                is_draft=False).order_by('-version').first()
            if not front_cfg or not back_cfg or event.branch not in (front_cfg.branch,back_cfg.branch):
                continue
            if stack.deployments.filter(status__in=OPEN).exists():
                break
            try:
                user = stack.project.created_by
                front_sha = _select_branch_commit(user,front_cfg)
                back_sha = _select_branch_commit(user,back_cfg)
                recent = stack.deployments.order_by('-created_at').first()
                if recent and recent.frontend_sha == front_sha and recent.backend_run.commit_sha == back_sha:
                    event.status = GitWebhookEvent.Status.HANDLED
                    event.error_message = 'LATEST_COMMITS_ALREADY_ATTEMPTED'
                else:
                    launch_stack(user,stack,idempotency_key=str(event.id),trigger=DeploymentRun.Trigger.GIT_PUSH,
                                 event=event,commits=(front_sha,back_sha))
                    event.status = GitWebhookEvent.Status.HANDLED
                    event.error_message = ''
                event.processed_at = timezone.now()
                event.save(update_fields=['status','error_message','processed_at'])
                count += 1
            except Exception as exc:
                logger.warning('Stack push %s en attente (%s)', event.pk, type(exc).__name__)
            break
    return count


def reconcile_stack_heads(*, offset=0, limit=2):
    """Fallback for missing webhooks; no replay of already-attempted commit pairs."""
    from apps.projects.configurations.stack_services import launch_stack
    from apps.deployments.runs.services import _select_branch_commit
    count = 0
    eligible = ServiceStack.objects.filter(auto_deploy=True, project__status='ACTIVE').order_by('pk')
    total = eligible.count()
    if not total:
        return {'created': 0, 'next_offset': 0}
    start = offset % total
    ids = list(eligible.values_list('pk', flat=True)[start:start + limit])
    if len(ids) < limit:
        ids += list(eligible.values_list('pk', flat=True)[:min(limit-len(ids), total-len(ids))])
    for stack in ServiceStack.objects.filter(pk__in=ids).select_related('project'):
        # Never start an unverified first production publish merely because a
        # configuration was saved; the owner explicitly starts its first run.
        if not stack.deployments.exists():
            continue
        if stack.deployments.filter(status__in=OPEN).exists():
            continue
        front = DeploymentConfig.objects.filter(environment=stack.frontend_environment,is_draft=False).order_by('-version').first()
        back = DeploymentConfig.objects.filter(environment=stack.backend_environment,is_draft=False).order_by('-version').first()
        if not front or not back:
            continue
        try:
            user = stack.project.created_by
            front_sha = _select_branch_commit(user,front)
            back_sha = _select_branch_commit(user,back)
            latest = stack.deployments.order_by('-created_at').first()
            if latest and (latest.frontend_sha,latest.backend_run.commit_sha) == (front_sha,back_sha):
                continue
            launch_stack(user,stack,idempotency_key='head:'+front_sha[:20]+':'+back_sha[:20],
                         trigger=DeploymentRun.Trigger.GIT_PUSH,commits=(front_sha,back_sha))
            count += 1
        except Exception as exc:
            logger.warning('Stack HEAD indisponible %s (%s)', stack.pk, type(exc).__name__)
    return {'created': count, 'next_offset': (start + len(ids)) % total}
