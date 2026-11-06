"""Idempotent domain reconciliation; executed by an isolated systemd timer."""
from django.core.management.base import BaseCommand
from apps.deployments.domains.reconciler import reconcile_domains

class Command(BaseCommand):
    help = 'Rattraper automatiquement les domaines DNS/HTTPS des projets en ligne.'
    def add_arguments(self, parser):
        parser.add_argument('--limit',type=int,default=5)
    def handle(self,*args,**options):
        report = reconcile_domains(limit=options['limit'])
        self.stdout.write('AUTOKOM_DOMAIN_RECONCILE inspected={inspected} attempted={attempted} activated={activated}'.format(**report))
