"""Initialize the FREE default offer required to create a customer project.
Does not overwrite an existing offer or invent any billing integration.
"""
from django.core.management.base import BaseCommand
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan


class Command(BaseCommand):
    help = 'Crée les offres de référence DevOps/DevSecOps et un plan Découverte gratuit à quotas limités, sans écraser le catalogue.'

    def handle(self, *args, **options):
        devops, _ = DeploymentModeOffer.objects.get_or_create(code='DEVOPS', defaults={
            'name': 'DevOps', 'description': 'Déploiement standard inclus.',
            'is_premium': False, 'is_active': True, 'is_default': True,
        })
        devsecops, _ = DeploymentModeOffer.objects.get_or_create(code='DEVSECOPS', defaults={
            'name': 'DevSecOps', 'description': 'Contrôles applicatifs premium.',
            'is_premium': True, 'is_active': True, 'is_default': False,
        })
        if not ResourcePlan.objects.filter(is_default=True, is_active=True, is_premium=False).exists():
            ResourcePlan.objects.get_or_create(code='starter', defaults={
                'name': 'Découverte', 'description': 'Hébergement statique Vite gratuit à quotas limités (250 mCPU, 512 Mo RAM, 1 Go stockage).',
                'cpu_millicores': 250, 'memory_mb': 512, 'storage_mb': 1024,
                'max_instances': 1, 'is_premium': False,
                'is_default': True, 'is_active': True,
            })
        self.stdout.write(self.style.SUCCESS('Offres initialisées. Vérifier les quotas dans Django Admin.'))
