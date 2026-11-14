from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError

from apps.deployments.domains.models import Domain
from apps.projects.common import validate_hostname
from apps.projects.project.models import Project
from apps.projects.project.services import retire_project


class Command(BaseCommand):
    help = 'Libère les domaines restés réservés par un projet déjà supprimé.'

    def add_arguments(self, parser):
        parser.add_argument('--hostname', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, hostname, apply, **options):
        try:
            host = validate_hostname(hostname)
        except ValidationError as exc:
            raise CommandError('Nom de domaine invalide.') from exc

        domain = Domain.objects.select_related('environment__project').filter(hostname=host).first()
        if domain is None:
            self.stdout.write(f'{host} · aucune réservation en base.')
            return
        project = domain.environment.project
        if project.status != Project.Status.DELETED:
            raise CommandError(f'{host} · le projet est {project.status}. Aucun changement.')
        hosts = Domain.objects.filter(environment__project=project).order_by('hostname')
        self.stdout.write(f'{project.name} · projet {project.pk} · organisation {project.organization_id}')
        for item in hosts:
            self.stdout.write(f'  {item.hostname} · {item.domain_type}')
        if not apply:
            self.stdout.write('Aperçu uniquement. Ajouter --apply pour libérer ces réservations.')
            return
        try:
            released = retire_project(project.pk, already_deleted=True)
        except ValidationError as exc:
            detail = exc.detail.get('project', exc.detail) if isinstance(exc.detail, dict) else exc.detail
            if isinstance(detail, list):
                detail = ' '.join(map(str, detail))
            raise CommandError(str(detail)) from exc
        self.stdout.write(self.style.SUCCESS(f'{released} réservation(s) libérée(s).'))
