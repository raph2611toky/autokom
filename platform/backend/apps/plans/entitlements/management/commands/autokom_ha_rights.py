"""Read-only entitlement diagnostics for AUTOKOM administrative operators.

Never grants paid rights and never changes customer deployments.
"""
from django.core.management.base import BaseCommand, CommandError
from apps.projects.environments.models import Environment
from apps.projects.configurations.models import DeploymentConfig


class Command(BaseCommand):
    help = 'Inspect the real plan and HA option granted to an AUTOKOM environment (read-only).'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--environment', help='UUID of the environment')
        group.add_argument('--project', help='UUID of the project (all environments)')
        group.add_argument('--list', action='store_true', help='List the last 25 environments')

    def handle(self, *args, **options):
        envs = Environment.objects.select_related('project').order_by('-created_at')
        if options['environment']:
            envs = envs.filter(pk=options['environment'])
        elif options['project']:
            envs = envs.filter(project_id=options['project'])
        else:
            envs = envs[:25]
        rows = list(envs)
        if not rows:
            raise CommandError('Aucun environnement trouvé pour cette sélection.')
        for env in rows:
            entitlement = getattr(env, 'entitlement', None)
            option = getattr(env, 'ha_option', None)
            cfg = DeploymentConfig.objects.filter(environment=env).order_by('-version').first()
            requested = bool(cfg and cfg.high_availability_requested)
            plan = entitlement.resource_plan if entitlement else None
            self.stdout.write('Projet={}  environnement={}  id={}'.format(
                env.project.name, env.slug, env.pk))
            self.stdout.write('  Demande HA={}  configuration={}'.format(
                requested, (('brouillon' if cfg.is_draft else 'finalisée') if cfg else 'aucune')))
            self.stdout.write('  Plan accordé={}  instances_max={}  statut={}  origine={}'.format(
                plan.code if plan else 'aucun', plan.max_instances if plan else 0,
                entitlement.status if entitlement else 'aucun',
                entitlement.grant_source if entitlement else 'aucune'))
            self.stdout.write('  Option HA={}  répliques={}'.format(
                option.status if option else 'absente', option.desired_replicas if option else 0))
            if requested and (not entitlement or entitlement.status != 'ACTIVE' or not plan or plan.max_instances < 2):
                self.stdout.write(self.style.WARNING('  BLOQUANT : aucun plan actif autorisant deux instances.'))
            elif requested and (not option or option.status != 'ACTIVE'):
                self.stdout.write(self.style.WARNING('  BLOQUANT : option HA non activée.'))
            elif requested:
                self.stdout.write(self.style.SUCCESS('  Droits HA présents (vérifier les dates d’expiration et la disponibilité du cloud).'))
            else:
                self.stdout.write('  Standard : 1 instance, aucune HA implicite.')
