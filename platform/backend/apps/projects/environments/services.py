from django.db import transaction
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError, NotFound
from apps.projects.environments.models import Environment, EnvironmentVariable
from apps.projects.secrets.models import SecretReference
from apps.projects.project.services import free_entitlement
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.common import get_environment


@transaction.atomic
def create_environment(project, user, attrs):
    slug = slugify(attrs['name'])[:100]
    if not slug or Environment.objects.filter(project=project, slug=slug).exists():
        raise ValidationError({'name': 'Nom d’environnement invalide ou déjà utilisé.'})
    env = Environment.objects.create(project=project, name=attrs['name'], slug=slug, kind=attrs['kind'])
    free_entitlement(env)
    DeploymentConfig.objects.create(environment=env, created_by=user, version=1, branch=project.default_branch)
    return env


def get_variable_or_404(env, variable_id):
    obj = EnvironmentVariable.objects.filter(environment=env, pk=variable_id).first()
    if obj is None:
        raise NotFound('Variable introuvable.')
    return obj


def validate_variable(attrs, env, *, exclude=None):
    secret = None
    if attrs.get('secret_id'):
        secret = SecretReference.objects.filter(pk=attrs['secret_id'], project=env.project, organization=env.project.organization,
             is_active=True).first()
        if secret is None or (secret.scope == 'ENVIRONMENT' and secret.environment_id != env.id):
            raise ValidationError({'secret_id': 'Secret indisponible pour cet environnement.'})
    elif any(s in attrs.get('key', '').upper() for s in ('PASSWORD', 'SECRET', 'TOKEN', 'PRIVATE_KEY', 'API_KEY')):
        raise ValidationError({'key': 'Cette variable semble sensible : créer un SecretReference et la lier ici.'})
    qs = EnvironmentVariable.objects.filter(environment=env, key=attrs['key'])
    if exclude:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise ValidationError({'key': 'Variable déjà définie.'})
    return secret
