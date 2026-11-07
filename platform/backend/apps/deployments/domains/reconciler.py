"""Autonomous DNS/HTTPS reconciliation for existing, healthy releases.

No Docker/Nginx/Cloudflare commands run in Django. The root broker owns all host
mutations; every failure is recorded by a safe code and retried with backoff.
"""
import logging
import re
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from apps.deployments.domains.models import Domain
from apps.deployments.domains.services import activate_managed_domains_for_release
from apps.deployments.runs.autokom_socket import AutokomControlUnavailable, request_control

logger = logging.getLogger('autokom.domain.reconcile')
SAFE = re.compile(r'^AUTOKOM_[A-Z0-9_]{3,80}$')
RETRY = timedelta(minutes=5)


def matches_active_release(runtime, environment_id, release):
    """Only repair the image registered as the active release; no CI or rollback here."""
    return (isinstance(runtime, dict)
            and runtime.get("environment_id") == str(environment_id)
            and release is not None
            and str(release.source_deployment_id) == runtime.get("run_id"))


def reconcile_domains(*, limit=5, now=None):
    """Repair old reserved domains and missing public vhosts without a Jenkins rebuild.

    Only a running production HostedApplication with an active Release is eligible.
    One attempt per environment; no secret or raw Cloudflare response is logged.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('Nombre de projets invalide.')
    now = now or timezone.now()
    qs = (Domain.objects.filter(
        domain_type=Domain.Type.SUBDOMAIN, is_disabled=False,
        environment__project__status='ACTIVE', environment__kind='PRODUCTION',
        environment__hosted_application__observed_state='RUNNING',
        environment__hosted_application__current_release__status='ACTIVE',
    ).select_related('environment', 'environment__hosted_application__current_release').order_by('created_at'))
    checked = attempted = repaired = 0
    processed = set()
    from apps.deployments.runs.models import DeploymentRun
    # All active sites are inspected, but only stale/broken sites are retried.
    for domain in qs[:250]:
        if attempted >= limit: break
        env = domain.environment
        if env.pk in processed: continue
        processed.add(env.pk)
        checked += 1
        if DeploymentRun.objects.filter(environment=env,status__in=['QUEUED','RUNNING']).exists():
            continue
        needs_repair = domain.status != Domain.Status.ACTIVE or domain.tls_status != 'ACTIVE'
        if not needs_repair:
            try:
                inspection = request_control('domain.inspect', {
                    'hostname':domain.hostname, 'environment_id':str(env.pk)}, timeout=10)
                needs_repair = inspection.get('active') is not True
            except AutokomControlUnavailable:
                needs_repair = True
        if not needs_repair: continue
        if domain.last_activation_attempt_at and now-domain.last_activation_attempt_at < RETRY:
            continue
        # A short DB compare-and-update gate prevents two timer invocations from
        # racing. Do not hold DB locks through external API or Certbot calls.
        with transaction.atomic():
            fresh = Domain.objects.select_for_update().get(pk=domain.pk)
            if fresh.last_activation_attempt_at and now-fresh.last_activation_attempt_at < RETRY:
                continue
            fresh.last_activation_attempt_at = now
            fresh.save(update_fields=['last_activation_attempt_at'])
        attempted += 1
        try:
            # Check that a real runtime exists before touching DNS/ACME.
            state = request_control('runtime.status',{'environment_id':str(env.pk)},timeout=12).get('runtime')
            if not matches_active_release(state, env.pk, env.hosted_application.current_release):
                raise AutokomControlUnavailable('Runtime et publication active incohérents.')
            activate_managed_domains_for_release(env)
            repaired += 1
            logger.info('AUTOKOM_DOMAIN_RECONCILED environment=%s',env.pk)
        except Exception as exc:
            code = getattr(exc,'safe_code',None)
            if not isinstance(code,str) or not SAFE.fullmatch(code):
                code = 'AUTOKOM_DOMAIN_RECONCILE_PENDING'
            Domain.objects.filter(pk=domain.pk).update(activation_error_code=code)
            logger.warning('AUTOKOM_DOMAIN_RECONCILE_PENDING code=%s environment=%s',code,env.pk)
    return {'inspected':checked,'attempted':attempted,'activated':repaired}
