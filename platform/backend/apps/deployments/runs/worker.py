"""DB-backed worker. Runs in a separate systemd service, NEVER inside HTTP views."""
import logging
import re
from django.db import transaction
from django.utils import timezone
from django.db.models import Max, Q
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, DeploymentLogEntry
from apps.deployments.runs.jenkins import JenkinsClient, JenkinsUnavailable

logger = logging.getLogger('autokom.deployments')
SECRETS = re.compile(r'(?i)((?:authorization\s*:\s*bearer|password|token|secret|api[_-]?key)\s*[=:]\s*)(\S+)')
BEARER = re.compile(r'(?i)\b(?:gh[us]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|xsmtpsib-[^\s]+)\b')


def redact(text):
    value = str(text).replace('\x00','')[:800]
    value = SECRETS.sub(r'\1[REDACTED]', value)
    value = BEARER.sub('[REDACTED]', value)
    return ''.join(c for c in value if c.isprintable() or c == '\t')


def add_log(run, line, level='INFO'):
    line = redact(line)
    # Only trusted agent markers, never arbitrary stdout from customer builds.
    if not line.startswith(('AUTOKOM_LOG:', 'AUTOKOM_STAGE:')) and level != 'ERROR':
        return
    # Single worker per deployment, bounded per-run log count.
    last = DeploymentLogEntry.objects.filter(deployment=run).aggregate(last=Max('sequence'))['last'] or 0
    if last >= 10000:
        return
    DeploymentLogEntry.objects.create(deployment=run, sequence=last+1, level=level, message=line)
    stage = re.search(r'^AUTOKOM_STAGE:(source|sast|sca|build|release|health):(RUNNING|SUCCESS|FAILED|SKIPPED)$', line)
    if stage:
        key, status = stage.groups()
        updates = {'status': status}
        if status == 'RUNNING':
            updates['started_at'] = timezone.now()
        else:
            updates['finished_at'] = timezone.now()
        PipelineStageRun.objects.filter(deployment=run, stage_key=key).update(**updates)


def dispatch_one(client):
    with transaction.atomic():
        run = (DeploymentRun.objects.select_for_update(skip_locked=True)
               .filter(status=DeploymentRun.Status.QUEUED).order_by('created_at').first())
        if not run:
            return False
        if run.cancel_requested:
            run.status = DeploymentRun.Status.CANCELLED
            run.finished_at = timezone.now()
            run.save(update_fields=['status','finished_at'])
            return True
        run.status = DeploymentRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=['status','started_at'])
    # Fail closed if dispatch outcome is unknown: no duplicate build on retry.
    try:
        external_id = client.dispatch(run)
        DeploymentRun.objects.filter(pk=run.pk, status='RUNNING').update(external_run_id=external_id)
        add_log(run, 'AUTOKOM_LOG: Exécution Jenkins mise en file ; aucun jeton GitHub transmis.')
    except Exception as exc:
        logger.warning('Dispatch failed for run %s: %s', run.pk, type(exc).__name__)
        DeploymentRun.objects.filter(pk=run.pk).update(status='BLOCKED', finished_at=timezone.now(),
              error_message='Dispatch Jenkins indisponible ou résultat incertain. Vérifier le job avant de retenter.')
        add_log(run, 'AUTOKOM_LOG: Dispatch impossible ; vérifier Jenkins (aucune relance automatique).', level='ERROR')
    return True


def poll_one(client, run_id):
    from datetime import timedelta
    now = timezone.now()
    with transaction.atomic():
        run = (DeploymentRun.objects.select_for_update(skip_locked=True)
               .filter(pk=run_id, status='RUNNING')
               .filter(Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now)).first())
        if run is None:
            return False
        run.next_poll_at = now + timedelta(seconds=12)
        run.save(update_fields=['next_poll_at'])
    if not run or not run.external_run_id:
        return False
    try:
        if run.cancel_requested:
            client.cancel(run.external_run_id)
            DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
                status='CANCELLED', finished_at=timezone.now())
            add_log(run, 'AUTOKOM_LOG: Annulation demandée par un utilisateur autorisé.')
            return True
        state = client.get_state(run.external_run_id)
        if state['external_id'].startswith('B:'):
            contents, offset = client.console(state['external_id'], run.console_offset)
            if offset > run.console_offset and contents:
                # Only full lines are released to clients; partial lines wait for next poll.
                combined = (run.console_pending + contents)[:68000]
                lines = combined.split('\n')
                for line in lines[:-1][:250]:
                    marker = re.search(r'(AUTOKOM_(?:LOG|STAGE):[^\r\n]{1,700})', line)
                    if marker:
                        add_log(run, marker.group(1))
                DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
                    console_offset=offset, console_pending=lines[-1][:2000])
        update = {}
        if state['external_id'] != run.external_run_id:
            update['external_run_id'] = state['external_id']
        if state['status'] != 'RUNNING':
            if run.console_pending:
                marker = re.search(r'(AUTOKOM_(?:LOG|STAGE):[^\r\n]{1,700})', run.console_pending)
                if marker:
                    add_log(run, marker.group(1))
                update['console_pending'] = ''
            if state['status'] == 'SUCCEEDED':
                try:
                    _release_docker_and_proxy(run)
                    register_release(run)
                    from apps.deployments.runs.autokom_socket import request_control
                    request_control('runtime.commit',{'run_id':str(run.pk),'environment_id':str(run.environment_id)},timeout=25)
                except Exception as exc:
                    # Never leave an unfinished local Docker release as the desired state.
                    try:
                        from apps.deployments.runs.autokom_socket import request_control
                        request_control('runtime.rollback',{
                            'run_id':str(run.id),'environment_id':str(run.environment_id)},timeout=35)
                    except Exception:
                        logger.warning('Rollback manuel requis pour %s',run.pk)
                    logger.warning('Release AUTOKOM refusée pour %s (%s)',run.pk,type(exc).__name__)
                    state['status']='FAILED'
                    update['error_message']='Déploiement Docker, vérification ou TLS impossible. Vérifier le broker.'
            update['status'] = state['status']
            update['finished_at'] = timezone.now()
        if update:
            DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(**update)
        if state['status'] != 'RUNNING':
            add_log(run, 'AUTOKOM_LOG: Exécution Jenkins terminée : ' + state['status'])
        return True
    except (JenkinsUnavailable, ValueError) as exc:
        logger.warning('Jenkins status unavailable for run %s: %s', run_id, type(exc).__name__)
        return False



def _release_docker_and_proxy(run):
    """Runs only in background worker; every host operation goes through the socket."""
    from apps.deployments.runs.autokom_socket import request_control
    from apps.deployments.runs.runner_manifest import fetch_runner_manifest
    from apps.deployments.domains.models import Domain
    from apps.deployments.domains.services import _challenge
    manifest=fetch_runner_manifest(run.id,include_secrets=True)
    resource=manifest['resources']
    cfg=manifest['build']
    if resource.get('replicas') != 1:
        raise ValueError('Haute disponibilité interdite avant activation GCP.')
    PipelineStageRun.objects.filter(deployment=run,stage_key='release').update(status='RUNNING',started_at=timezone.now())
    result=request_control('runtime.deploy',{
        'run_id':str(run.pk),'environment_id':str(run.environment_id),
        'cpu_millicores':int(resource['cpu_millicores']),
        'memory_mb':int(resource['memory_mb']),'storage_mb':int(resource['storage_mb']),
        'internal_port':int(cfg['port']),'healthcheck':cfg['healthcheck'],
        'replicas':1,'variables':manifest['variables'],
    },timeout=280)
    PipelineStageRun.objects.filter(deployment=run,stage_key='release').update(status='SUCCESS',finished_at=timezone.now())
    PipelineStageRun.objects.filter(deployment=run,stage_key='health').update(status='RUNNING',started_at=timezone.now())
    domains=Domain.objects.filter(environment=run.environment,verified_at__isnull=False,status__in=[Domain.Status.VERIFYING,Domain.Status.ACTIVE])
    for domain in domains:
        request_control('domain.activate',{
            'hostname':domain.hostname,'environment_id':str(run.environment_id),
            'managed':domain.domain_type==Domain.Type.SUBDOMAIN,
            'token':_challenge(domain),
        },timeout=160)
        domain.status=Domain.Status.ACTIVE
        domain.tls_status='ACTIVE'
        domain.save(update_fields=['status','tls_status'])
    PipelineStageRun.objects.filter(deployment=run,stage_key='health').update(status='SUCCESS',finished_at=timezone.now())
    add_log(run,'AUTOKOM_STAGE:release:SUCCESS')
    add_log(run,'AUTOKOM_STAGE:health:SUCCESS')
    add_log(run,'AUTOKOM_LOG: Image Docker locale, endpoint privé et TLS contrôlés.')


def register_release(run):
    from apps.deployments.releases.models import Release
    from apps.deployments.sites.models import HostedApplication
    from django.conf import settings
    if Release.objects.filter(source_deployment=run).exists():
        return
    with transaction.atomic():
        previous = Release.objects.filter(environment=run.environment, status='ACTIVE').first()
        if previous:
            previous.status = Release.Status.SUPERSEDED
            previous.save(update_fields=['status'])
        image_ref = f'autokom-local:{run.id}'
        release = Release.objects.create(environment=run.environment, source_deployment=run,
              previous_release=previous, version=str(run.id), image_reference=image_ref,
              status=Release.Status.ACTIVE, published_at=timezone.now())
        HostedApplication.objects.update_or_create(environment=run.environment, defaults={
            'current_release': release, 'observed_state': HostedApplication.Status.RUNNING,
            'health_status': 'HEALTHY', 'last_health_checked_at': timezone.now()})

def run_tick():
    from datetime import timedelta
    # A network failure after POST may have left an unknown Jenkins queue ID.
    # Never retry blindly (could create a second production deployment).
    DeploymentRun.objects.filter(status='RUNNING', external_run_id='',
        started_at__lt=timezone.now()-timedelta(minutes=4)).update(
        status='BLOCKED', finished_at=timezone.now(),
        error_message='Dispatch incertain : rapprochement manuel requis dans Jenkins.')
    client = JenkinsClient()
    dispatched = dispatch_one(client)
    # Check a bounded batch; never starve later running jobs.
    polled = False
    for run_id in DeploymentRun.objects.filter(status='RUNNING').exclude(external_run_id='').order_by('created_at').values_list('pk', flat=True)[:20]:
        polled = poll_one(client, run_id) or polled
    return bool(dispatched or polled)
