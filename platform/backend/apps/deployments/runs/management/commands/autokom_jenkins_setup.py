"""Deprecated: Jenkins jobs are managed by AUTOKOM Core, never inside Django."""
from django.core.management.base import BaseCommand,CommandError

class Command(BaseCommand):
    help = 'Voir sudo autokom runtime jenkins --check|--apply'

    def handle(self,*args,**kwargs):
        raise CommandError('Utiliser sudo autokom runtime jenkins --apply. Aucune action Jenkins depuis Django.')
