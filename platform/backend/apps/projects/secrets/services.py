from rest_framework.exceptions import ValidationError, NotFound
from apps.projects.git.services import encrypt
from apps.projects.secrets.models import SecretReference
from apps.projects.environments.models import Environment


def get_secret(project, secret_id):
    obj = SecretReference.objects.filter(project=project, pk=secret_id).first()
    if obj is None:
        raise NotFound('Secret introuvable.')
    return obj


def secret_environment(project, attrs):
    environment = None
    if attrs.get('environment_id'):
        environment = Environment.objects.filter(project=project, pk=attrs['environment_id']).first()
        if environment is None:
            raise ValidationError({'environment_id': 'Environnement introuvable.'})
    return environment


def ensure_unique_secret(project, environment, name):
    if SecretReference.objects.filter(project=project, environment=environment, name=name).exists():
        raise ValidationError({'name': 'Un secret portant ce nom existe déjà.'})
