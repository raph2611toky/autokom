"""Separate least-privilege systemd service: python manage.py autokom_deployment_worker."""
import time
from django.core.management.base import BaseCommand
from apps.deployments.runs.worker import run_tick

class Command(BaseCommand):
    help = 'Consume la file de déploiement AUTOKOM (Jenkins dédié).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=5)

    def handle(self, *args, **options):
        if not 2 <= options['interval'] <= 120:
            raise ValueError('Intervalle entre 2 et 120 secondes.')
        while True:
            try:
                run_tick()
            except Exception as exc:
                # Do not leak credentials / HTTP response bodies in systemd journal.
                self.stderr.write(f'AUTOKOM worker : {type(exc).__name__} (voir journal applicatif protégé).')
                if options['once']:
                    raise
            if options['once']:
                return
            time.sleep(options['interval'])
