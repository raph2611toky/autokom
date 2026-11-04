"""Contrôle d'accès aux objets clients : jamais d'accès cross-organisation."""
import re
from pathlib import PurePosixPath
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.organizations.models import Organization, OrganizationMembership
from apps.projects.project.models import Project, ProjectMember
from apps.projects.environments.models import Environment

ORG_READ = ('OWNER', 'ADMIN', 'DEVELOPER', 'VIEWER')
ORG_WRITE = ('OWNER', 'ADMIN', 'DEVELOPER')
PROJECT_WRITE = ('MAINTAINER', 'DEVELOPER')


def organizations_for_user(user, *, write=False):
    qs = Organization.objects.filter(is_active=True).filter(
        Q(owner=user) | Q(memberships__user=user, memberships__is_active=True,
                           memberships__role__in=ORG_WRITE if write else ORG_READ)
    )
    return qs.distinct()


def get_organization(user, organization_id, *, write=False):
    org = organizations_for_user(user, write=write).filter(pk=organization_id).first()
    if org is None:
        raise NotFound('Organisation introuvable ou accès non autorisé.')
    return org


def projects_for_user(user, *, write=False):
    if write:
        org_q = Q(organization__owner=user) | Q(
            organization__memberships__user=user,
            organization__memberships__is_active=True,
            organization__memberships__role__in=ORG_WRITE)
        member_q = Q(members__user=user, members__role__in=PROJECT_WRITE)
    else:
        org_q = Q(organization__owner=user) | Q(
            organization__memberships__user=user, organization__memberships__is_active=True)
        member_q = Q(members__user=user)
    return Project.objects.filter(status=Project.Status.ACTIVE, organization__is_active=True).filter(
        org_q | member_q
    ).distinct()


def get_project(user, project_id, *, write=False):
    project = projects_for_user(user, write=write).select_related('organization', 'git_connection').filter(pk=project_id).first()
    if project is None:
        raise NotFound('Projet introuvable ou accès non autorisé.')
    return project


def get_environment(user, project_id, environment_id, *, write=False):
    project = get_project(user, project_id, write=write)
    env = Environment.objects.filter(project=project, pk=environment_id).first()
    if env is None:
        raise NotFound('Environnement introuvable.')
    return project, env


def safe_relative_path(value, *, allow_empty=True):
    value = str(value or '').strip().replace('\\', '/')
    if not value and allow_empty:
        return ''
    if not value or value.startswith('/') or '\x00' in value or '\n' in value:
        raise ValidationError('Chemin relatif invalide.')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts) or ':' in parts[0]:
        raise ValidationError('Le chemin ne doit contenir ni traversée ni chemin absolu.')
    return value


def default_hostname(project_slug):
    return f'{project_slug}.{settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX}'.lower()


def validate_hostname(hostname):
    hostname = str(hostname or '').strip().lower().rstrip('.')
    try:
        hostname = hostname.encode('idna').decode('ascii')
    except (UnicodeError, ValueError):
        raise ValidationError({'domain': 'Nom de domaine invalide.'})
    if len(hostname) > 253 or not re.fullmatch(
        r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', hostname
    ):
        raise ValidationError({'domain': 'Nom de domaine invalide.'})
    if hostname in ('localhost',) or hostname.endswith('.localhost'):
        raise ValidationError({'domain': 'Ce domaine est réservé.'})
    return hostname


def int_param(request, name, *, default, minimum=1, maximum=1000):
    try:
        v = int(request.query_params.get(name, default))
    except (ValueError, TypeError):
        raise ValidationError({name: 'Entier attendu.'})
    if not minimum <= v <= maximum:
        raise ValidationError({name: f'Valeur autorisée entre {minimum} et {maximum}.'})
    return v
