"""Reconcile launch-offer deadlines. Dry-run by default; preserve customer data.

Usage: python manage.py autokom_mvp_reconcile [--apply]

The HA public LB must have a verified disable/delivery-withdraw operation before
turning on HA teardown. This command refuses to change any HA environment.
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.projects.project.models import Project
from apps.deployments.domains.models import Domain
from apps.deployments.runs.models import DeploymentRun
from apps.deployments.runs.autokom_socket import request_control
from apps.plans.entitlements.mvp import enabled, window_for_project

logger = logging.getLogger('autokom.mvp.lifecycle')


class Command(BaseCommand):
    help = 'Inspect and optionally suspend expired MVP public routes; never purge automatically'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Disable owned local domains (not HA)')
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **opts):
        if not enabled():
            self.stdout.write('MVP disabled; no changes')
            return
        if not 1 <= opts['limit'] <= 500:
            raise ValueError('Invalid limit')
        scanned = suspended = blocked_ha = 0
        now = timezone.now()
        projects = Project.objects.filter(
            environments__deployment_runs__status=DeploymentRun.Status.SUCCEEDED,
        ).distinct().order_by('created_at')[:opts['limit']]
        for project in projects:
            scanned += 1
            window = window_for_project(project, now=now)
            if window['state'] not in ('grace', 'purge_due'):
                continue
            if project.status != Project.Status.ACTIVE:
                continue
            # The cloud MIG manifest must be withdrawn and its public LB probe
            # verified before suspending an HA customer, otherwise it stays live.
            from apps.projects.configurations.models import DeploymentConfig, ServiceStack
            from apps.plans.entitlements.models import HighAvailabilityOption
            ha = (ServiceStack.objects.filter(project=project, high_availability_requested=True).exists()
                  or DeploymentConfig.objects.filter(
                      environment__project=project, is_draft=False,
                      high_availability_requested=True).exists()
                  or HighAvailabilityOption.objects.filter(
                      environment__project=project, status=HighAvailabilityOption.Status.ACTIVE).exists())
            if ha:
                blocked_ha += 1
                self.stdout.write(f'SKIP HA project={project.pk}: cloud delivery withdrawal not yet implemented')
                continue
            for domain in Domain.objects.filter(environment__project=project):
                # Retry proxy withdrawal if an earlier broker call failed.
                self.stdout.write(f'EXPIRED project={project.pk} domain={domain.hostname} '
                                  f'grace_until={window["purge_after"].isoformat()} '
                                  f'apply={bool(opts["apply"])}')
                if not opts['apply']:
                    continue
                # Close reconciliation before removing a managed route. A broker
                # failure leaves disabled=True; the next job can retry after
                # operator review without reactivating the public domain.
                with transaction.atomic():
                    current = Domain.objects.select_for_update().get(pk=domain.pk)
                    if not current.is_disabled:
                        current.is_disabled = True
                        current.save(update_fields=['is_disabled', 'updated_at'])
                try:
                    request_control('domain.deactivate', {
                        'hostname': domain.hostname,
                        'environment_id': str(domain.environment_id),
                    }, timeout=25)
                except Exception as exc:
                    logger.warning('MVP_DOMAIN_SUSPEND_PENDING project=%s cause=%s', project.pk,
                                   type(exc).__name__)
                    self.stderr.write(f'SUSPEND_PENDING project={project.pk} domain={domain.hostname}')
                    continue
                suspended += 1
            if window['state'] == 'purge_due':
                self.stdout.write(f'PURGE_DUE project={project.pk}: manual review required; no container deletion')
        self.stdout.write(f'MVP reconcile inspected={scanned} suspended={suspended} skipped_ha={blocked_ha} '
                          f'mode={"apply" if opts["apply"] else "check"}')
