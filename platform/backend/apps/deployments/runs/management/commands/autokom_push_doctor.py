"""Inspect automatic GitHub deployments, optionally reconcile current HEAD.

No API tokens, webhook secrets, request bodies or secret variables are printed.
Read-only unless --apply is explicitly provided by an AUTOKOM administrator.
"""
import re
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.projects.project.models import Project
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.git.models import GitWebhookEvent
from apps.deployments.runs.models import DeploymentRun
from apps.deployments.runs.push_reconcile import reconcile_branch_heads


class Command(BaseCommand):
    help = 'Diagnostiquer les déploiements automatiques GitHub, sans afficher de secrets.'

    def add_arguments(self, parser):
        parser.add_argument('--repository', required=True, help='owner/repository')
        parser.add_argument('--commit', default='', help='SHA GitHub de la publication à vérifier')
        parser.add_argument('--apply', action='store_true', help='Comparer le HEAD distant et programmer le déploiement manquant si autorisé')

    def handle(self, *args, **options):
        repository = options['repository'].strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', repository):
            raise CommandError('Nom owner/repository invalide.')
        commit = options['commit'].lower()
        if commit and not re.fullmatch(r'[a-f0-9]{7,40}', commit):
            raise CommandError('SHA GitHub invalide.')
        self.stdout.write('AUTOKOM · GitHub push · ' + repository)
        self.stdout.write('Webhook secret : ' + ('configuré' if settings.GITHUB_WEBHOOK_SECRET else 'ABSENT'))
        projects = Project.objects.filter(repository_name__iexact=repository).select_related('git_connection')
        if not projects.exists():
            self.stdout.write('Aucun projet AUTOKOM associé à ce dépôt. Vérifier l’installation GitHub.')
        for project in projects:
            conn = project.git_connection
            self.stdout.write(f'Projet : {project.name} · statut={project.status} · connexion={"active" if conn and conn.is_active else "absente/inactive"} · installation={"présente" if conn and conn.installation_id else "absente"}')
            for env in project.environments.filter(is_active=True):
                cfg = DeploymentConfig.objects.filter(environment=env, is_draft=False).order_by('-version').first()
                publications = DeploymentRun.objects.filter(environment=env)
                if commit:
                    publications = publications.filter(commit_sha__startswith=commit)
                last = publications.order_by('-created_at').first()
                self.stdout.write(f'  {env.name} : auto_push={bool(cfg and cfg.auto_deploy)} · branche={cfg.branch if cfg else "aucune configuration finalisée"} · suivi={cfg.auto_deploy_branch if cfg else "-"}')
                if last:
                    origin = ('WEBHOOK_SIGNÉ' if last.webhook_event_id else 'RATTRAPAGE_HEAD') if last.trigger_type == 'GIT_PUSH' else last.trigger_type
                    reference = last.external_run_id or ''
                    reference = reference if re.fullmatch(r'[BQ]:[1-9][0-9]{0,9}', reference) else '-'
                    self.stdout.write(f'  Publication : {last.status} · origine={origin} · commit={last.commit_sha[:12]} · Jenkins={reference}')
                elif commit:
                    self.stdout.write('  Aucune publication pour ce SHA.')
        events = GitWebhookEvent.objects.filter(repository_full_name__iexact=repository)
        if commit:
            events = events.filter(commit_sha__startswith=commit)
        events = events.order_by('-received_at')[:10]
        n = 0
        for e in events:
            n += 1
            self.stdout.write(f'  Webhook : {e.received_at:%Y-%m-%d %H:%M:%S UTC} · {e.event_type} · {e.branch} · {e.status} · {e.commit_sha[:12]} · {e.error_message[:80] or "OK"}')
        if not n:
            self.stdout.write('Aucun webhook reçu en base pour ce dépôt. Vérifier GitHub App > Advanced > Recent deliveries.')
        if options['apply']:
            result = reconcile_branch_heads(offset=0, limit=30, repository=repository)
            self.stdout.write(f'Vérification du HEAD GitHub : environnements={result["examined"]} · nouveaux déploiements={result["created"]}')
