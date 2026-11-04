"""Orchestration client: validations, quotas, file d'attente durable, zéro shell HTTP."""
import hashlib
import re
import secrets
from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError, PermissionDenied, NotFound
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun
from apps.projects.common import get_environment
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.configurations.services import check_configuration, check_entitlement, validate_branch
from apps.projects.git.models import GitWebhookEvent
from apps.projects.git.services import get_branches
from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption

SHA_RE = re.compile(r'^[a-fA-F0-9]{40}$')
IDEMPOTENCY_RE = re.compile(r'^[a-zA-Z0-9_.:-]{12,128}$')


class DeploymentConflict(APIException):
    status_code = 409
    default_detail = 'Déploiement impossible dans cet état.'


def get_final_config(env):
    cfg = DeploymentConfig.objects.filter(environment=env, is_draft=False).order_by('-version').first()
    if cfg is None:
        raise DeploymentConflict('Finaliser une configuration avant de déployer.')
    return cfg


def effective_replicas(env, plan):
    option = HighAvailabilityOption.objects.filter(environment=env).first()
    if option is None or option.status in (HighAvailabilityOption.Status.REJECTED,
                                            HighAvailabilityOption.Status.EXPIRED):
        return 1
    if option.status != HighAvailabilityOption.Status.ACTIVE:
        # Pending purchase doesn't interrupt existing single-replica operations.
        return 1
    raise DeploymentConflict('Haute disponibilité : option visible mais non activable avant le cluster GCP.')


def plan_deployment(env, *, verify_remote=False, user=None):
    if not env.is_active or env.project.status != 'ACTIVE':
        raise DeploymentConflict('Projet ou environnement désactivé.')
    project = env.project
    if not project.git_connection or not project.git_connection.is_active or not project.git_connection.installation_id:
        raise DeploymentConflict('Connecter une installation GitHub autorisée au projet.')
    if not str(project.repository_external_id).isdigit() or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', project.repository_name):
        raise DeploymentConflict('Dépôt GitHub manquant ou mal configuré.')
    cfg = get_final_config(env)
    issues = check_configuration(cfg)
    if issues:
        raise ValidationError(issues)
    plan = check_entitlement(cfg)
    replicas = effective_replicas(env, plan)
    # Reject stale/unreadable secrets BEFORE queuing a build or contacting Jenkins.
    from apps.projects.environments.models import EnvironmentVariable
    from apps.projects.git.services import decrypt
    for var in EnvironmentVariable.objects.filter(environment=env, secret__isnull=False).select_related('secret'):
        sec = var.secret
        if not sec.is_active or sec.project_id != project.id or (sec.environment_id and sec.environment_id != env.id):
            raise DeploymentConflict('Secret non autorisé pour cet environnement.')
        if sec.vault_reference or not sec.encrypted_value:
            raise DeploymentConflict('Coffre de secrets externe non connecté au runner.')
        try:
            decrypt(sec.encrypted_value)  # decryptability check only; never expose values
        except APIException as exc:
            raise DeploymentConflict('Secret illisible : clé de chiffrement ou valeur à vérifier.') from exc
    if verify_remote:
        validate_branch(user, cfg)
    return cfg, {'cpu_millicores': plan.cpu_millicores, 'memory_mb': plan.memory_mb,
                 'storage_mb': plan.storage_mb, 'replicas': replicas,
                 'resource_plan': plan.code, 'mode': cfg.mode}


def _select_branch_commit(user, cfg):
    project = cfg.environment.project
    for page in range(1, 21):
        _, branches = get_branches(user, project.git_connection.installation_id, project.repository_external_id, page)
        for branch in branches:
            if branch.get('name') == cfg.branch:
                sha = (branch.get('commit') or {}).get('sha')
                if not sha or not SHA_RE.fullmatch(sha):
                    raise DeploymentConflict('Révision GitHub invalide ou inaccessible.')
                return sha.lower()
        if len(branches) < 100:
            break
    raise DeploymentConflict('Branche sélectionnée introuvable ou non autorisée par GitHub.')


def ensure_runner_ready():
    from apps.deployments.runs.jenkins import verify_settings
    verify_settings()  # local Unix socket + approved Jenkins service account
    key = getattr(settings, 'AUTOKOM_RUNNER_SHARED_KEY', '')
    if len(key) < 32:
        raise DeploymentConflict('Clé interne du runner Jenkins manquante.')


def _create_run(env, cfg, resources, *, actor, trigger, sha, idem, event=None):
    run = DeploymentRun.objects.create(
        environment=env, configuration=cfg, entitlement=env.entitlement,
        requested_by=actor, webhook_event=event, trigger_type=trigger,
        mode_snapshot=cfg.mode, resources_snapshot=resources,
        commit_sha=sha, branch=cfg.branch, idempotency_key=idem,
        ci_provider='JENKINS', status=DeploymentRun.Status.QUEUED)
    stage_specs = [('source','Récupération GitHub','git'), ('sast','SAST / Semgrep','semgrep'),
                   ('sca','SCA / Trivy','trivy'), ('build','Build isolé','buildkit'),
                   ('release','Docker privé · AUTOKOM','docker'),
                   ('health','Healthcheck & Nginx TLS','nginx')]
    if cfg.mode == 'DEVOPS':
        stage_specs = [st for st in stage_specs if st[0] not in ('sast', 'sca')]
    PipelineStageRun.objects.bulk_create([
        PipelineStageRun(deployment=run, stage_key=k, name=n, position=i, tool_name=tool)
        for i, (k,n,tool) in enumerate(stage_specs, start=1)])
    return run


def queue_manual(user, project_id, environment_id, *, idempotency_key=None):
    _, env = get_environment(user, project_id, environment_id, write=True)
    if idempotency_key and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise ValidationError({'idempotency_key': 'Entre 12 et 128 caractères alphanumériques/._:-.'})
    idem = f'manual:{env.id}:{user.id}:{idempotency_key or secrets.token_urlsafe(24)}'
    existing = DeploymentRun.objects.filter(idempotency_key=idem).first()
    if existing:
        return existing, False
    # GitHub network calls MUST NOT run with an InnoDB row lock held.
    cfg, resources = plan_deployment(env)
    ensure_runner_ready()
    sha = _select_branch_commit(user, cfg)
    with transaction.atomic():
        env = type(env).objects.select_for_update().get(pk=env.pk)
        existing = DeploymentRun.objects.filter(idempotency_key=idem).first()
        if existing:
            return existing, False
        if DeploymentRun.objects.filter(environment=env, status__in=['QUEUED','RUNNING']).exists():
            raise DeploymentConflict('Un déploiement est déjà en cours sur cet environnement.')
        current_cfg, current_resources = plan_deployment(env)
        if current_cfg.pk != cfg.pk or current_resources != resources:
            raise DeploymentConflict('Configuration ou droits modifiés : actualiser et relancer le déploiement.')
        return _create_run(env, cfg, resources, actor=user, trigger=DeploymentRun.Trigger.MANUAL,
                           sha=sha, idem=idem), True


@transaction.atomic
def queue_verified_push(event):
    """Webhook signé en amont; un seul enregistrement par livraison GitHub."""
    from apps.projects.project.models import Project
    from apps.projects.environments.models import Environment
    event = GitWebhookEvent.objects.select_for_update().get(pk=event.pk)
    if event.status != GitWebhookEvent.Status.RECEIVED or not event.signature_verified:
        return 0
    if (event.event_type != 'push' or not SHA_RE.fullmatch(event.commit_sha or '') or
            event.commit_sha == '0' * 40):
        event.status = GitWebhookEvent.Status.IGNORED
        event.processed_at = timezone.now()
        event.save(update_fields=['status','processed_at'])
        return 0
    created = 0
    projects = Project.objects.filter(git_connection=event.connection, status=Project.Status.ACTIVE,
                                     repository_external_id=event.repository_external_id,
                                     repository_name=event.repository_full_name)
    for project in projects:
        for env in Environment.objects.filter(project=project, is_active=True):
            cfg = DeploymentConfig.objects.filter(environment=env, is_draft=False).order_by('-version').first()
            if not cfg or not cfg.auto_deploy or (cfg.auto_deploy_branch or cfg.branch) != event.branch:
                continue
            if DeploymentRun.objects.filter(environment=env, status__in=['QUEUED','RUNNING']).exists():
                continue  # Next webhook gets its own delivery; no destructive interruption.
            try:
                checked_cfg, resources = plan_deployment(env)
                ensure_runner_ready()
            except (APIException, ValidationError):
                continue  # No entitlement / capacity / runner. No privilege escalation.
            idem = f'push:{event.id}:{env.id}'
            if DeploymentRun.objects.filter(idempotency_key=idem).exists():
                continue
            _create_run(env, checked_cfg, resources, actor=None,
                        trigger=DeploymentRun.Trigger.GIT_PUSH,
                        sha=event.commit_sha.lower(), idem=idem, event=event)
            created += 1
    event.status = GitWebhookEvent.Status.HANDLED if created else GitWebhookEvent.Status.IGNORED
    event.processed_at = timezone.now()
    event.save(update_fields=['status', 'processed_at'])
    return created


def request_ha(env, replicas):
    if replicas < 2 or replicas > 20:
        raise ValidationError({'replicas': 'Entre 2 et 20 réplicas.'})
    with transaction.atomic():
        obj, created = HighAvailabilityOption.objects.select_for_update().get_or_create(
            environment=env, defaults={'desired_replicas': replicas})
        if not created and obj.status == HighAvailabilityOption.Status.ACTIVE:
            raise DeploymentConflict('Option HA déjà activée : contactez le support pour modifier le contrat.')
        obj.desired_replicas = replicas
        obj.status = HighAvailabilityOption.Status.PENDING
        obj.approved_by = None
        obj.billing_reference = ''
        obj.activated_at = None
        obj.expires_at = None
        obj.save(update_fields=['desired_replicas','status','approved_by','billing_reference','activated_at','expires_at','updated_at'])
        return obj
