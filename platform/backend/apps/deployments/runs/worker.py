"""DB-backed worker. Runs in a separate systemd service, NEVER inside HTTP views."""
import logging
import re
import time
from django.db import transaction
from django.utils import timezone
from django.db.models import Max, Q
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, DeploymentLogEntry
from apps.deployments.runs.jenkins import JenkinsClient, JenkinsUnavailable
from apps.deployments.runs.autokom_socket import AutokomControlUnavailable
from apps.deployments.runs.console_stream import consume_diagnostic_chunk

logger = logging.getLogger('autokom.deployments')
_NEXT_GITHUB_HEAD_CHECK = 0.0
_GITHUB_HEAD_CHECK_OFFSET = 0
SECRETS = re.compile(r'(?i)((?:authorization\s*:\s*bearer|password|token|secret|api[_-]?key)\s*[=:]\s*)(\S+)')
BEARER = re.compile(r'(?i)\b(?:gh[us]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|xsmtpsib-[^\s]+)\b')


def redact(text):
    value = str(text).replace('\x00','')[:800]
    value = SECRETS.sub(r'\1[REDACTED]', value)
    value = BEARER.sub('[REDACTED]', value)
    return ''.join(c for c in value if c.isprintable() or c == '\t')


def add_customer_trace(run, stage, channel, message):
    """Store an additional *trusted*, per-run line for the technical file view.

    Only AUTOKOM-authored, bounded messages may use this helper. Never pass
    repository stdout, subprocess command lines, variables or user-supplied
    exception strings here. The existing summary stream remains unchanged.
    """
    if stage not in {'source', 'sast', 'sca', 'build', 'release', 'health'}:
        raise ValueError('Stage de trace invalide.')
    if channel not in {'pipeline', 'broker', 'docker', 'application'}:
        raise ValueError('Origine de trace invalide.')
    if not isinstance(message, str) or not message or len(message) > 540 or '\n' in message or '\r' in message:
        raise ValueError('Trace client invalide.')
    add_log(run, f'AUTOKOM_TRACE:{stage}:{channel}:{message}', level='DETAIL')


def add_log(run, line, level='INFO'):
    line = redact(line)
    # Markers are informational only. Never use customer-originated log text as
    # proof that a security scan or deployment has passed.
    if not line.startswith(('AUTOKOM_LOG:', 'AUTOKOM_STAGE:', 'AUTOKOM_TECH:', 'AUTOKOM_TRACE:')) and level != 'ERROR':
        return
    # Serialize sequence allocation per deployment across multiple workers.
    # Prevents duplicate sequence IDs during overlapping log polling.
    with transaction.atomic():
        DeploymentRun.objects.select_for_update().only('pk').get(pk=run.pk)
        last = (DeploymentLogEntry.objects.filter(deployment=run)
                .aggregate(last=Max('sequence'))['last'] or 0)
        if last >= 10000:
            return
        DeploymentLogEntry.objects.create(
            deployment=run, sequence=last + 1, level=level, message=line)
        stage = re.search(
            r'^AUTOKOM_STAGE:(source|sast|sca|build|release|health):'
            r'(RUNNING|SUCCESS|FAILED|SKIPPED)$', line)
        if stage:
            key, status = stage.groups()
            # Display state only. Never authorize a release from log markers.
            updates = {'status': status}
            if status == 'RUNNING':
                updates['started_at'] = timezone.now()
            else:
                updates['finished_at'] = timezone.now()
            PipelineStageRun.objects.filter(
                deployment=run, stage_key=key).update(**updates)


def ingest_console(run_id, text, offset, *, final=False):
    """Atomically checkpoint a Jenkins byte offset, pending line, and markers.

    No Jenkins network request happens while holding a MySQL row lock.
    """
    with transaction.atomic():
        current = (DeploymentRun.objects.select_for_update()
                   .filter(pk=run_id, status='RUNNING').first())
        if current is None:
            return False
        if offset < current.console_offset:
            return False
        if offset == current.console_offset and not final:
            return False
        if offset == current.console_offset and text:
            # A stale duplicate chunk must never append duplicate markers.
            return False
        records, pending = consume_diagnostic_chunk(current.console_pending, text, final=final)
        # The stage context is derived from trusted stage events already saved
        # for this run, not from any user-controlled Jenkins stdout.
        previous = (DeploymentLogEntry.objects.filter(deployment=current,
                    message__startswith='AUTOKOM_STAGE:').order_by('-sequence')
                    .values_list('message', flat=True).first())
        stage_key = previous.split(':')[1] if previous else 'source'
        for record in records:
            if record.startswith('AUTOKOM_STAGE:'):
                stage_key = record.split(':')[1]
                add_log(current, record)
            elif record.startswith('AUTOKOM_TECH:'):
                add_log(current, 'AUTOKOM_TECH:' + stage_key + ':' + record[len('AUTOKOM_TECH:'):], level='TECH')
                if 'Référence d’identifiants du job introuvable' in record:
                    add_log(current, 'AUTOKOM_LOG: Le service de publication doit être configuré par un administrateur.')
            else:
                add_log(current, record)
        current.console_offset = offset
        current.console_pending = pending
        current.save(update_fields=['console_offset', 'console_pending'])
        return True


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
        # Every hosted project reserves an AUTOKOM-managed domain. Refuse a
        # Jenkins build *before* consuming CI minutes when DNS credentials
        # are missing. The privileged broker validates the root-only config.
        from apps.deployments.runs.autokom_socket import request_control
        from apps.deployments.domains.models import Domain
        from django.conf import settings
        managed_hosts = list(Domain.objects.filter(
            environment_id=run.environment_id, domain_type=Domain.Type.SUBDOMAIN,
            is_disabled=False).order_by('hostname').values_list('hostname', flat=True)[:21])
        if not managed_hosts or len(managed_hosts) > 20:
            error = AutokomControlUnavailable('Réservation du domaine AUTOKOM absente ou incohérente.')
            error.safe_code = 'AUTOKOM_DOMAIN_RESERVATION_INVALID'
            raise error
        request_control('domain.config.check', {
            'managed_suffix': settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX,
            'managed_hosts': managed_hosts,
        }, timeout=10)
        add_customer_trace(run, 'source', 'pipeline', 'Contrôle de la réservation du domaine réussi avant mise en file du build.')
        external_id = client.dispatch(run)
        DeploymentRun.objects.filter(pk=run.pk, status='RUNNING').update(external_run_id=external_id)
        add_log(run, 'AUTOKOM_LOG: Exécution Jenkins mise en file ; aucun jeton GitHub transmis.')
        add_customer_trace(run, 'source', 'pipeline', 'La demande de compilation a été acceptée par Jenkins ; suivi des étapes démarré.')
    except Exception as exc:
        code = getattr(exc, 'safe_code', '')
        is_cf = code in {'AUTOKOM_DOMAIN_CLOUDFLARE_CONFIG_MISSING','AUTOKOM_CF_SECRETS_PERMISSIONS',
                         'AUTOKOM_CF_SECRETS_INVALID','AUTOKOM_DOMAIN_SUFFIX_MISSING',
                         'AUTOKOM_INGRESS_IPV4_MISSING','AUTOKOM_CF_ZONE_MISMATCH',
                         'AUTOKOM_DOMAIN_SUFFIX_MISMATCH','AUTOKOM_DOMAIN_PREFLIGHT_INVALID',
                         'AUTOKOM_DOMAIN_RESERVATION_INVALID','AUTOKOM_RUNTIME_ENV_PERMISSIONS',
                         'AUTOKOM_RUNTIME_ENV_INVALID','AUTOKOM_ACME_EMAIL_MISSING'}
        logger.warning('Dispatch blocked for run %s: %s code=%s', run.pk, type(exc).__name__,
                       code if is_cf else 'AUTOKOM_DISPATCH_BLOCKED')
        DeploymentRun.objects.filter(pk=run.pk).update(status='BLOCKED', finished_at=timezone.now(),
              error_message=('AUTOKOM_DOMAIN_PREFLIGHT_BLOCKED '+code if is_cf else
                             'Dispatch Jenkins indisponible ou résultat incertain. Vérifier le job avant de retenter.'))
        if is_cf:
            advice = ('autokom runtime env show / edit AUTOKOM_MANAGED_DOMAIN_SUFFIX'
                      if code in {'AUTOKOM_DOMAIN_SUFFIX_MISMATCH', 'AUTOKOM_DOMAIN_SUFFIX_MISSING'}
                      else 'autokom dns cloudflare env show / autokom runtime env show')
            add_log(run, 'AUTOKOM_LOG: Publication bloquée avant Jenkins : '+code+
                    ' · administrateur : '+advice+'.', level='ERROR')
        else:
            add_log(run, 'AUTOKOM_LOG: Dispatch impossible ; vérifier Jenkins (aucune relance automatique).', level='ERROR')
    return True


def poll_one(client, run_id):
    """Poll Jenkins, persist console markers, then reconcile Docker/DB safely."""
    from datetime import timedelta
    now = timezone.now()
    with transaction.atomic():
        run = (DeploymentRun.objects.select_for_update(skip_locked=True)
               .filter(pk=run_id, status='RUNNING')
               .filter(Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now))
               .first())
        if run is None:
            return False
        run.next_poll_at = now + timedelta(seconds=12)
        run.save(update_fields=['next_poll_at'])
    if not run.external_run_id:
        return False
    try:
        if run.cancel_requested:
            client.cancel(run.external_run_id)
            DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
                status='CANCELLED', finished_at=timezone.now())
            add_log(run, 'AUTOKOM_LOG: Annulation demandée par un utilisateur autorisé.')
            return True

        state = client.get_state(run.external_run_id)
        external_id = state['external_id']
        # A missing/offline Jenkins agent must never leave a client RUNNING forever.
        # Re-read the queue state immediately before cancelling so a started build
        # is not treated as a queue timeout. Never retry dispatch automatically.
        if (run.external_run_id.startswith('Q:') and external_id.startswith('Q:')
                and run.started_at and now - run.started_at > timedelta(minutes=8)):
            try:
                client.cancel(run.external_run_id)
            except (JenkinsUnavailable, AutokomControlUnavailable):
                logger.warning('Cancellation of stale queue item not confirmed for %s', run_id)
                return False
            DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
                status='BLOCKED', finished_at=timezone.now(),
                error_message='La préparation n’a pas pu démarrer. Réessayez après rétablissement du service.')
            PipelineStageRun.objects.filter(deployment=run, status='PENDING').update(
                status='SKIPPED', finished_at=timezone.now())
            add_log(run, 'AUTOKOM_LOG: Délai de démarrage dépassé ; exécution annulée.')
            return True
        terminal = state['status'] != 'RUNNING'
        # Persist the B:... identifier before collecting logs, so a crash can
        # resume from the same build rather than restarting a Jenkins job.
        if external_id != run.external_run_id:
            DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
                external_run_id=external_id)

        if external_id.startswith('B:'):
            # Jenkins progressiveText may return multiple chunks even AFTER the
            # build has finished. Do not finalize until the console is drained.
            # Limit each tick to three bounded reads to prevent starvation.
            for _ in range(3):
                fresh = DeploymentRun.objects.filter(pk=run_id, status='RUNNING').first()
                if fresh is None:
                    return False
                text, offset = client.console(external_id, fresh.console_offset)
                if offset < fresh.console_offset:
                    raise ValueError('Offset Jenkins non monotone.')
                if offset > fresh.console_offset:
                    ingest_console(run_id, text, offset, final=False)
                # A maximum-size chunk might have more data behind it. Finalize
                # in a subsequent tick rather than silently skipping log lines.
                if len(text.encode('utf-8')) >= 31_000:
                    if terminal:
                        return True
                    continue
                if terminal:
                    latest = DeploymentRun.objects.filter(pk=run_id, status='RUNNING').first()
                    if latest:
                        ingest_console(run_id, '', latest.console_offset, final=True)
                break

        if not terminal:
            return True
        run = DeploymentRun.objects.filter(pk=run_id, status='RUNNING').first()
        if run is None:
            return False
        if run.cancel_requested:
            # A late cancellation must not publish a completed Jenkins build.
            DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
                status='CANCELLED', finished_at=timezone.now())
            add_log(run, 'AUTOKOM_LOG: Annulation enregistrée avant publication.')
            return True

        status = state['status']
        error_message = ''
        if status == 'FAILED':
            # A useful status without disclosing Jenkins internals or secrets.
            # More specifics are available in the filtered technical log.
            error_message = ('La préparation du projet a échoué. '
                             'Consultez le journal technique pour identifier l’étape concernée.')
        if status == 'SUCCEEDED':
            from apps.deployments.runs.autokom_socket import request_control
            try:
                add_log(run, 'AUTOKOM_TECH:release:Publication contrôlée démarrée (Docker, DNS et HTTPS).', level='TECH')
                add_customer_trace(run, 'release', 'broker', 'Début de transaction de publication : image locale, conteneur isolé, relais HTTP privé.')
                _release_docker_and_proxy(run)
                add_log(run, 'AUTOKOM_TECH:release:Runtime et domaine vérifiés ; validation de la version active.', level='TECH')
                add_customer_trace(run, 'health', 'broker', 'Contrôles terminés : runtime interne et domaine HTTPS validés avant activation définitive.')
                # The broker may still roll back at this point. Do NOT publish
                # the release as ACTIVE in MySQL before this commit succeeds.
                request_control('runtime.commit', {
                    'run_id': str(run.pk),
                    'environment_id': str(run.environment_id),
                }, timeout=25)
                add_log(run, 'AUTOKOM_TECH:release:Publication et validation du runtime terminées.', level='TECH')
                add_customer_trace(run, 'release', 'broker', 'Validation finale enregistrée ; cette version peut devenir la publication active.')
            except Exception as exc:
                code=getattr(exc, 'safe_code', None)
                safe_code = code if isinstance(code,str) and re.fullmatch(r'AUTOKOM_[A-Z0-9_]{3,80}',code) else 'AUTOKOM_PUBLISH_FAILED'
                add_log(run, 'AUTOKOM_TRACE:release:broker:Échec de publication · '+safe_code, level='DETAIL')
                add_customer_trace(run, 'release', 'broker', 'Le contrôle de santé a refusé la version candidate ; collecte des erreurs de démarrage avant nettoyage.')
                try:
                    diagnostic=request_control('runtime.diagnostics', {
                        'run_id':str(run.pk), 'environment_id':str(run.environment_id),
                    },timeout=12)
                    if diagnostic.get('available'):
                        status=str(diagnostic.get('container_status','unknown'))
                        if status not in {'running','exited','restarting','dead','created','paused','unknown','unavailable'}:
                            status='unknown'
                        exit_code=diagnostic.get('exit_code')
                        add_log(run, 'AUTOKOM_TRACE:release:docker:État='+status+' · code de sortie='+(
                            str(exit_code) if type(exit_code) is int else 'inconnu')+
                            ' · OOM='+('oui' if diagnostic.get('oom_killed') else 'non'), level='DETAIL')
                        add_customer_trace(run, 'release', 'docker', 'Résultat de l’inspection du conteneur enregistré avant le nettoyage de la tentative.')
                        for line in diagnostic.get('lines', [])[:24]:
                            if isinstance(line,str) and len(line)<=260:
                                add_log(run, 'AUTOKOM_TRACE:release:application:'+line, level='DETAIL')
                        # Safe, targeted interpretation of a known startup error.
                        # The original sanitized exception remains available above.
                        if any('sqlite3.OperationalError: unable to open database file' in line
                               for line in diagnostic.get('lines', []) if isinstance(line, str)):
                            add_customer_trace(run, 'release', 'application',
                                'SQLite : vérifier le chemin et les droits. Le système de fichiers applicatif est en lecture seule ; utiliser un emplacement temporaire inscriptible ou une base externe.')
                except Exception:
                    logger.warning('Diagnostic conteneur indisponible pour %s',run.pk)
                add_log(run, 'AUTOKOM_TECH:release:Échec lors de la publication ; code='+safe_code, level='TECH')
                add_customer_trace(run, 'release', 'broker', 'La version candidate n’a pas été activée ; restauration ou conservation de la publication précédente.')
                try:
                    request_control('runtime.rollback', {
                        'run_id': str(run.pk),
                        'environment_id': str(run.environment_id),
                    }, timeout=35)
                    add_log(run, 'AUTOKOM_TECH:release:Retour arrière demandé et accepté par le broker.', level='TECH')
                except Exception:
                    add_log(run, 'AUTOKOM_TECH:release:Retour arrière non confirmé ; réconciliation requise.', level='TECH')
                    logger.warning('Réconciliation Docker manuelle requise pour %s', run.pk)
                logger.warning('Publication AUTOKOM refusée pour %s (%s)',
                               run.pk, type(exc).__name__)
                status = 'FAILED'
                code=getattr(exc, 'safe_code', None)
                error_message = 'La mise en ligne a échoué. La version précédente est conservée.'
                if code: error_message += ' Code de diagnostic : ' + code
                PipelineStageRun.objects.filter(deployment=run,
                    stage_key__in=('release', 'health')).update(
                        status='FAILED', finished_at=timezone.now())
            else:
                try:
                    # After runtime.commit, the broker can have deleted the old
                    # container. Never attempt a blind rollback on DB failure.
                    register_release(run)
                except Exception as exc:
                    logger.error('Réconciliation release MySQL requise pour %s (%s)',
                                 run.pk, type(exc).__name__)
                    status = 'BLOCKED'
                    error_message = ('Docker publié, état MySQL à réconcilier. '
                                     'Ne pas redéployer avant diagnostic.')

        # A terminal Jenkins failure must not leave progress indicators stuck at RUNNING.
        # Missing stage markers are *never* considered evidence of success.
        if status in ('FAILED', 'BLOCKED', 'CANCELLED'):
            PipelineStageRun.objects.filter(deployment=run, status='RUNNING').update(
                status='FAILED' if status == 'FAILED' else 'SKIPPED', finished_at=timezone.now())
            PipelineStageRun.objects.filter(deployment=run, status='PENDING').update(
                status='SKIPPED', finished_at=timezone.now())
        DeploymentRun.objects.filter(pk=run_id, status='RUNNING').update(
            status=status, finished_at=timezone.now(), error_message=error_message)
        add_log(run, 'AUTOKOM_LOG: Exécution terminée : ' + status)
        return True
    except (JenkinsUnavailable, AutokomControlUnavailable, ValueError) as exc:
        logger.warning('État Jenkins temporairement indisponible pour %s (%s)',
                       run_id, type(exc).__name__)
        return False


def _release_docker_and_proxy(run):
    """Runs only in background worker; every host operation goes through the socket."""
    from apps.deployments.runs.autokom_socket import request_control
    from apps.deployments.runs.runner_manifest import fetch_runner_manifest
    from apps.deployments.domains.models import Domain
    from apps.deployments.domains.services import _challenge, activate_managed_domains_for_release
    manifest=fetch_runner_manifest(run.id,include_secrets=True)
    resource=manifest['resources']
    cfg=manifest['build']
    if resource.get('replicas') != 1:
        raise ValueError('Haute disponibilité interdite avant activation GCP.')
    PipelineStageRun.objects.filter(deployment=run,stage_key='release').update(status='RUNNING',started_at=timezone.now())
    add_log(run, 'AUTOKOM_TECH:release:Docker · import image, lancement du conteneur et contrôle HTTP interne.', level='TECH')
    add_customer_trace(run, 'release', 'broker', f'Ressources retenues : CPU={int(resource["cpu_millicores"])}m, RAM={int(resource["memory_mb"])} MiB, stockage temporaire={int(resource["storage_mb"])} MiB.')
    add_customer_trace(run, 'release', 'docker', f'Publication sur port privé {int(cfg["port"])} ; 1 conteneur isolé, sans exposition TCP directe.')
    add_customer_trace(run, 'release', 'broker', 'Le conteneur doit répondre au contrôle HTTP interne avant l’activation du domaine.')
    result=request_control('runtime.deploy',{
        'run_id':str(run.pk),'environment_id':str(run.environment_id),
        'cpu_millicores':int(resource['cpu_millicores']),
        'memory_mb':int(resource['memory_mb']),'storage_mb':int(resource['storage_mb']),
        'internal_port':int(cfg['port']),'healthcheck':cfg['healthcheck'],
        'replicas':1,'variables':manifest['variables'],
    },timeout=280)
    add_log(run, 'AUTOKOM_TECH:release:Docker · conteneur et contrôle HTTP interne validés.', level='TECH')
    add_customer_trace(run, 'release', 'docker', 'Réponse HTTP interne conforme : la nouvelle version est candidate à l’activation HTTPS.')
    # The container is a candidate, not a committed public release yet.
    # Do not show 'Publication: Terminé' until TLS and the public site pass.
    PipelineStageRun.objects.filter(deployment=run,stage_key='health').update(status='RUNNING',started_at=timezone.now())
    # Managed subdomains are activated automatically after the first healthy Docker build.
    # Failure aborts the release so that SUCCEEDED never means an inaccessible public site.
    # Reconcile old projects created with only a custom domain.
    if not Domain.objects.filter(environment=run.environment, domain_type=Domain.Type.SUBDOMAIN).exists():
        from apps.projects.project.services import reserve_domain
        reserve_domain(run.environment.project, run.environment)
    add_log(run, 'AUTOKOM_TECH:health:Domaine public · vérification DNS, configuration Nginx et certificat HTTPS.', level='TECH')
    add_customer_trace(run, 'health', 'broker', 'Vérification HTTPS et du reverse proxy en cours ; pas de basculement anticipé.')
    activate_managed_domains_for_release(run.environment)
    add_log(run, 'AUTOKOM_TECH:health:Domaine public · DNS et HTTPS activés.', level='TECH')
    add_customer_trace(run, 'health', 'broker', 'Réconciliation du domaine géré terminée : terminaison TLS et réponse HTTPS contrôlées.')
    domains=Domain.objects.filter(environment=run.environment,domain_type=Domain.Type.CUSTOM,verified_at__isnull=False,status__in=[Domain.Status.VERIFYING,Domain.Status.ACTIVE])
    for domain in domains:
        request_control('domain.activate',{
            'hostname':domain.hostname,'environment_id':str(run.environment_id),
            'managed':domain.domain_type==Domain.Type.SUBDOMAIN,
            'token':_challenge(domain),
        },timeout=720)
        domain.status=Domain.Status.ACTIVE
        domain.tls_status='ACTIVE'
        domain.save(update_fields=['status','tls_status'])
    PipelineStageRun.objects.filter(deployment=run,stage_key='release').update(status='SUCCESS',finished_at=timezone.now())
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
    from apps.deployments.runs.push_reconcile import reconcile_received_pushes, reconcile_branch_heads
    global _NEXT_GITHUB_HEAD_CHECK, _GITHUB_HEAD_CHECK_OFFSET
    # Retry HMAC-verified events on each tick (database only).
    reconcile_received_pushes(limit=12)
    # The remote GitHub API can be slow. Never block polling or dispatch of an
    # active CI job while checking for missing webhook deliveries. Only check
    # a small batch during idle periods; runs are still validated/queued with
    # the ordinary AUTOKOM entitlement and source authorization rules.
    busy = DeploymentRun.objects.filter(status__in=['QUEUED', 'RUNNING']).exists()
    if not busy and time.monotonic() >= _NEXT_GITHUB_HEAD_CHECK:
        _NEXT_GITHUB_HEAD_CHECK = time.monotonic() + 90
        try:
            head_status = reconcile_branch_heads(offset=_GITHUB_HEAD_CHECK_OFFSET, limit=3)
            _GITHUB_HEAD_CHECK_OFFSET = head_status['next_offset']
        except Exception as exc:
            logger.warning('GitHub HEAD reconciliation unavailable (%s); retry scheduled.', type(exc).__name__)
    # A network failure after POST may have left an unknown Jenkins queue ID.
    # Never retry blindly (could create a second production deployment).
    DeploymentRun.objects.filter(status='RUNNING', external_run_id='',
        started_at__lt=timezone.now()-timedelta(minutes=4)).update(
        status='BLOCKED', finished_at=timezone.now(),
        error_message='Dispatch incertain : rapprochement manuel requis dans Jenkins.')
    # A health endpoint does not prove Jenkins/socket are available. Retry on
    # subsequent ticks instead of crashing the management command every 5s.
    if not DeploymentRun.objects.filter(status__in=['QUEUED', 'RUNNING']).exists():
        return False
    try:
        client = JenkinsClient()
    except (JenkinsUnavailable, AutokomControlUnavailable) as exc:
        logger.warning('Broker/Jenkins indisponible (%s). Nouvelle tentative au prochain tick.',
                       type(exc).__name__)
        return False
    dispatched = dispatch_one(client)
    # Check a bounded batch; never starve later running jobs.
    polled = False
    for run_id in DeploymentRun.objects.filter(status='RUNNING').exclude(external_run_id='').order_by('created_at').values_list('pk', flat=True)[:20]:
        polled = poll_one(client, run_id) or polled
    return bool(dispatched or polled)
