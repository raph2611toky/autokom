"""Explicit image rollback through the same protected publication transaction."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from apps.deployments.releases.models import Release
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, StackDeployment
from apps.deployments.runs.services import DeploymentConflict, IDEMPOTENCY_RE, plan_deployment, check_entitlement
from apps.projects.configurations.models import ServiceStack
from apps.projects.common import projects_for_user
from rest_framework.exceptions import ValidationError


def current_release(env):
    return Release.objects.filter(environment=env, status='ACTIVE',
        hosted_sites__environment=env, hosted_sites__observed_state='RUNNING').first()


def plan_restore(env, source, *, grouped=False):
    if not grouped and ServiceStack.objects.filter(project_id=env.project_id).exists():
        raise DeploymentConflict('La restauration de cette publication groupée doit couvrir les deux services.')
    latest_cfg, resources = plan_deployment(env)
    if (source.environment_id != env.pk or source.status != 'SUCCEEDED'
            or (latest_cfg.mode == 'DEVSECOPS' and source.mode_snapshot != 'DEVSECOPS')):
        raise DeploymentConflict('Version incompatible avec la politique de sécurité actuelle.')
    # Keep the historical image recipe and port, with the current resource
    # ceiling, current secrets and the same HA policy.
    check_entitlement(source.configuration)
    if latest_cfg.high_availability_requested != source.configuration.high_availability_requested:
        raise DeploymentConflict('Version incompatible avec la politique de disponibilité actuelle.')
    return resources


@transaction.atomic
def queue_restore(env, source, actor, key):
    if not isinstance(key, str) or len(key) > 48 or not IDEMPOTENCY_RE.fullmatch(key):
        raise ValidationError('Clé d’idempotence invalide.')
    project = env.project
    type(project).objects.select_for_update().only('pk').get(pk=project.pk)
    env = type(env).objects.select_for_update().get(pk=env.pk)
    idem = f'restore:{env.pk}:{source.pk}:{key}'
    existing = DeploymentRun.objects.filter(environment=env, idempotency_key=idem).first()
    if existing:
        return existing, False
    if DeploymentRun.objects.filter(environment=env, status__in=['QUEUED','RUNNING','PREPARED']).exists() or DeploymentRun.objects.filter(
            environment=env, status='BLOCKED', trigger_type='ROLLBACK', error_message='AUTOKOM_RESTORE_RECONCILE_REQUIRED').exists():
        raise DeploymentConflict('Une publication est déjà en cours.')
    active = current_release(env)
    if not active or not Release.objects.filter(environment=env, source_deployment=source, status='SUPERSEDED').exists():
        raise DeploymentConflict('Choisissez une ancienne version publiée dans cet environnement.')
    resources = plan_restore(env, source)
    return _create_restore(env, source, actor, idem, active, resources), True


def _create_restore(env, source, actor, idem, active, resources):
    run = DeploymentRun.objects.create(environment=env, configuration=source.configuration, entitlement=env.entitlement,
        requested_by=actor, trigger_type='ROLLBACK', rollback_source=source, mode_snapshot=source.mode_snapshot,
        resources_snapshot={**resources, 'restore_expected_current':str(active.source_deployment_id)},
        commit_sha=source.commit_sha, branch=source.branch, idempotency_key=idem, ci_provider='ARTIFACT',
        sast_report=source.sast_report, sca_report=source.sca_report)
    keys = [('release','Restauration de l’image publiée','docker'), ('health','Vérification du site','nginx')]
    if run.mode_snapshot == 'DEVSECOPS':
        keys.insert(1, ('dast','Contrôle dynamique HTTP','autokom-runtime'))
    PipelineStageRun.objects.bulk_create([PipelineStageRun(deployment=run, stage_key=k, name=n, position=i, tool_name=t)
                                          for i,(k,n,t) in enumerate(keys,1)])
    return run


@transaction.atomic
def queue_stack_restore(group, actor, key):
    if not isinstance(key, str) or len(key) > 48 or not IDEMPOTENCY_RE.fullmatch(key):
        raise ValidationError('Clé d’idempotence invalide.')
    stack = ServiceStack.objects.select_for_update().get(pk=group.stack_id)
    idem = f'group-restore:{group.pk}:{key}'
    existing = StackDeployment.objects.filter(stack=stack, idempotency_key=idem).first()
    if existing:
        return existing, False
    if group.status != 'SUCCEEDED' or not group.frontend_run_id or stack.deployments.filter(
            status__in=['QUEUED','PREPARING','ACTIVATING','BLOCKED']).exists():
        raise DeploymentConflict('Choisissez une ancienne publication groupée terminée.')
    envs = [stack.backend_environment, stack.frontend_environment]
    if DeploymentRun.objects.filter(environment__in=envs, status__in=['QUEUED','RUNNING','PREPARED']).exists():
        raise DeploymentConflict('Une publication est déjà en cours.')
    sources = [group.backend_run, group.frontend_run]
    active, resources = stack_restore_plan(stack, sources)
    back, front = [_create_restore(env, src, actor, idem+':'+role, current, resources)
                   for env,src,role,current in zip(envs,sources,('back','front'),active)]
    return StackDeployment.objects.create(stack=stack, backend_run=back, frontend_run=front,
        frontend_configuration=front.configuration, frontend_sha=front.commit_sha,
        frontend_resources=resources, idempotency_key=idem), True


def stack_restore_plan(stack, sources):
    from apps.projects.configurations.stack_services import _split_resources, stack_domain_hostname
    from apps.deployments.runs.services import get_final_config
    envs = (stack.backend_environment, stack.frontend_environment)
    active, resources = [], []
    for env, source in zip(envs, sources):
        if source.environment_id != env.pk or not Release.objects.filter(
                environment=env, source_deployment=source, status='SUPERSEDED').exists():
            raise DeploymentConflict('Les deux versions historiques doivent être conservées.')
        latest = get_final_config(env)
        if stack_domain_hostname(env, latest) != stack_domain_hostname(env, source.configuration):
            raise DeploymentConflict('Le domaine d’un service a changé depuis cette publication.')
        current = current_release(env)
        if not current:
            raise DeploymentConflict('Un service ne dispose pas de version active.')
        active.append(current)
        resources.append(plan_restore(env, source, grouped=True))
    return active, _split_resources(*resources)


def image_restore_parameters(run):
    original = run.rollback_source
    # A rollback of a rollback can fall back to the original Jenkins TAR.
    for _ in range(100):
        if original.environment_id != run.environment_id:
            break
        if not original.rollback_source_id:
            source = Release.objects.get(environment=run.environment, source_deployment=run.rollback_source, status='SUPERSEDED')
            return {'source_run':str(original.pk), 'expected_current':run.resources_snapshot['restore_expected_current'],
                    'image_digest':source.image_digest}
        original = original.rollback_source
    raise DeploymentConflict('Chaîne de restauration invalide.')


def actor_can_restore(run):
    return bool(run.requested_by and run.requested_by.is_active and projects_for_user(
        run.requested_by, write=True).filter(pk=run.environment.project_id).exists())


def flag_abandoned_stack_restores():
    cut = timezone.now() - timedelta(minutes=70)
    StackDeployment.objects.filter(status__in=['PREPARING','ACTIVATING'], backend_run__trigger_type='ROLLBACK',
        backend_run__started_at__lt=cut).update(status='BLOCKED',
            error_code='AUTOKOM_RESTORE_RECONCILE_REQUIRED', finished_at=timezone.now())


def process_stack_restore():
    from apps.deployments.runs.stack_worker import _activate_group, _fail_group
    from apps.deployments.runs.worker import _release_docker_and_proxy, add_log, logger
    with transaction.atomic():
        group = (StackDeployment.objects.select_for_update(skip_locked=True)
            .filter(status='QUEUED', backend_run__trigger_type='ROLLBACK').order_by('created_at').first())
        if not group:
            return False
        group.status = 'PREPARING'
        group.save(update_fields=['status'])
        runs = [group.backend_run,group.frontend_run]
        for run in runs:
            run.status = 'RUNNING'
            run.started_at = timezone.now()
            run.save(update_fields=['status','started_at'])
    try:
        if not all(actor_can_restore(run) for run in runs):
            raise DeploymentConflict('Droits de restauration modifiés.')
        active, resources = stack_restore_plan(group.stack, [run.rollback_source for run in runs])
        for current,run in zip(active,runs):
            if run.cancel_requested or run.resources_snapshot != {
                    **resources, 'restore_expected_current':str(current.source_deployment_id)}:
                raise DeploymentConflict('Les conditions de restauration ont changé.')
        for role, run in zip(('backend','frontend'),runs):
            run.refresh_from_db(fields=['cancel_requested'])
            if run.cancel_requested:
                raise DeploymentConflict('Restauration annulée.')
            add_log(run, 'AUTOKOM_LOG: Restauration groupée de l’image historique, sans reconstruction Git.')
            _release_docker_and_proxy(run, stack=group, stack_role=role, restore=image_restore_parameters(run))
            run.status = 'PREPARED'
            run.save(update_fields=['status'])
    except Exception as exc:
        logger.warning('Préparation de la restauration groupée %s refusée (%s)', group.pk, type(exc).__name__)
        code = getattr(exc,'safe_code',None) or 'AUTOKOM_STACK_RESTORE_FAILED'
        _fail_group(group, code=code)
        for run in runs:
            DeploymentRun.objects.filter(pk=run.pk,status='RUNNING').update(
                status='BLOCKED' if group.status == 'BLOCKED' else 'FAILED',
                finished_at=timezone.now(), error_message=code)
            run.stages.filter(status__in=['RUNNING','PENDING']).update(status='SKIPPED', finished_at=timezone.now())
        return True
    return _activate_group(group)


def process_restore():
    from apps.deployments.runs.worker import _release_docker_and_proxy, register_release, add_log, logger
    from apps.deployments.runs.autokom_socket import request_control
    with transaction.atomic():
        run = (DeploymentRun.objects.select_for_update(skip_locked=True).filter(trigger_type='ROLLBACK', status='QUEUED', backend_stack_execution__isnull=True, frontend_stack_execution__isnull=True)
               .order_by('created_at').first())
        if not run:
            return False
        run.status = 'CANCELLED' if run.cancel_requested else 'RUNNING'
        run.started_at = timezone.now()
        if run.cancel_requested:
            run.finished_at = run.started_at
        run.save(update_fields=['status','started_at','finished_at'])
    if run.cancel_requested:
        return True
    status, error = 'FAILED', 'AUTOKOM_RESTORE_FAILED'
    phase = 'preflight'
    try:
        if not actor_can_restore(run):
            raise DeploymentConflict('Droits de restauration modifiés.')
        resources = plan_restore(run.environment, run.rollback_source)
        if resources != {k:v for k,v in run.resources_snapshot.items() if k != 'restore_expected_current'}:
            raise DeploymentConflict('Les droits ou les ressources ont changé depuis la demande.')
        active = current_release(run.environment)
        expected = run.resources_snapshot['restore_expected_current']
        if not active or str(active.source_deployment_id) != expected:
            raise DeploymentConflict('La version active a changé depuis la demande.')
        restore = image_restore_parameters(run)
        add_log(run, 'AUTOKOM_LOG: Restauration de l’image historique, sans reconstruction Git.')
        phase = 'candidate'
        _release_docker_and_proxy(run, restore=restore)
        run.refresh_from_db(fields=['cancel_requested'])
        if run.cancel_requested:
            raise DeploymentConflict('Restauration annulée.')
        phase = 'commit'
        result = request_control('runtime.commit', {'run_id':str(run.pk), 'environment_id':str(run.environment_id)}, timeout=30)
        register_release(run, image_digest=result.get('image_digest', ''))
        status, error = 'SUCCEEDED', ''
    except Exception as exc:
        logger.warning('Restauration non confirmée run=%s phase=%s cause=%s', run.pk, phase, type(exc).__name__)
        code = getattr(exc, 'safe_code', None)
        if code:
            error = code
        if phase == 'candidate':
            try:
                request_control('runtime.rollback', {'run_id':str(run.pk), 'environment_id':str(run.environment_id)}, timeout=60)
            except Exception:
                status, error = 'BLOCKED', 'AUTOKOM_RESTORE_RECONCILE_REQUIRED'
        if phase == 'commit':
            status, error = 'BLOCKED', 'AUTOKOM_RESTORE_RECONCILE_REQUIRED'
        run.stages.filter(status__in=['RUNNING','SUCCESS']).update(status='FAILED', finished_at=timezone.now())
        run.stages.filter(status='PENDING').update(status='SKIPPED', finished_at=timezone.now())
    DeploymentRun.objects.filter(pk=run.pk, status='RUNNING').update(status=status, error_message=error, finished_at=timezone.now())
    add_log(run, 'AUTOKOM_LOG: Restauration ' + ('validée.' if status == 'SUCCEEDED' else 'non confirmée. ' + error))
    return True
