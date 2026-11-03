from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError, APIException
from apps.projects.common import get_organization, validate_hostname, default_hostname
from apps.projects.git.models import GitConnection
from apps.projects.git.services import get_repository
from apps.projects.project.models import Project
from apps.projects.environments.models import Environment
from apps.projects.configurations.models import DeploymentConfig
from apps.deployments.domains.models import Domain
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.plans.entitlements.models import ApplicationEntitlement


class MissingFreePlan(APIException):
    status_code = 409
    default_detail = 'Les offres gratuites ne sont pas encore configurées par AUTOKOM.'


def free_entitlement(environment):
    mode = DeploymentModeOffer.objects.filter(code='DEVOPS', is_active=True, is_premium=False).first()
    plan = ResourcePlan.objects.filter(is_default=True, is_active=True, is_premium=False).order_by('display_order').first()
    if mode is None or plan is None:
        raise MissingFreePlan()
    return ApplicationEntitlement.objects.create(
        environment=environment, mode_offer=mode, resource_plan=plan,
        status=ApplicationEntitlement.Status.ACTIVE,
        grant_source=ApplicationEntitlement.GrantSource.SYSTEM,
        activated_at=timezone.now(),
    )


def reserve_domain(project, environment, selected=''):
    hostname = validate_hostname(selected) if selected else validate_hostname(default_hostname(project.slug))
    from django.conf import settings
    if selected and (hostname == settings.AUTOKOM_BASE_DOMAIN or hostname.endswith('.' + settings.AUTOKOM_BASE_DOMAIN)):
        raise ValidationError({'domain': 'Choisir le sous-domaine automatique, pas un domaine AUTOKOM personnalisé.'})
    if Domain.objects.filter(hostname=hostname).exists():
        raise ValidationError({'domain': 'Ce nom de domaine est déjà réservé.'})
    return Domain.objects.create(
        organization=project.organization, environment=environment, hostname=hostname,
        domain_type=Domain.Type.CUSTOM if selected else Domain.Type.SUBDOMAIN,
        status=Domain.Status.PENDING,
    )


@transaction.atomic
def create_project(user, data):
    organization = get_organization(user, data['organization_id'], write=True)
    installation, repo = get_repository(user, data['installation_id'], data['repository_id'])
    name = data['name'].strip()
    slug = slugify(name)[:160]
    if not slug:
        raise ValidationError({'name': 'Nom du projet invalide.'})
    if Project.objects.filter(organization=organization, slug=slug).exists():
        raise ValidationError({'name': 'Nom de projet déjà utilisé dans cette organisation.'})
    account = installation.get('account') or {}
    connection, _ = GitConnection.objects.update_or_create(
        organization=organization, provider=GitConnection.Provider.GITHUB,
        external_account_id=str(account['id']),
        defaults={
            'created_by': user, 'account_name': account['login'],
            'installation_id': installation['id'], 'is_active': True,
        },
    )
    project = Project.objects.create(
        organization=organization, created_by=user, git_connection=connection,
        repository_external_id=str(repo['id']), repository_name=repo['full_name'],
        repository_url=repo['html_url'], default_branch=repo.get('default_branch') or 'main',
        name=name, slug=slug, description=data.get('description', ''),
    )
    environment = Environment.objects.create(project=project, name='Production', slug='production', kind=Environment.Kind.PRODUCTION)
    free_entitlement(environment)
    DeploymentConfig.objects.create(
        environment=environment, created_by=user, version=1, is_draft=True,
        branch=project.default_branch,
    )
    reserve_domain(project, environment, data.get('domain', ''))
    return project
