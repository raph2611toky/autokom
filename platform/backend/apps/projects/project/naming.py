"""Read-only production naming proposal shared by preview and creation."""
import re
from dataclasses import dataclass

from django.conf import settings
from django.utils.crypto import salted_hmac
from django.utils.text import slugify
from rest_framework.exceptions import APIException, ValidationError

from apps.deployments.domains.models import Domain
from apps.deployments.runs.autokom_socket import AutokomControlUnavailable, request_control
from apps.projects.common import default_hostname, get_organization, validate_hostname
from apps.projects.project.models import Project


class NameCheckUnavailable(APIException):
    status_code = 503
    default_detail = 'Vérification DNS momentanément indisponible. Réessayez avant de continuer.'


@dataclass(frozen=True)
class ProjectAddress:
    slug: str
    hostname: str
    suffixed: bool


def normalized_project_label(name):
    label = re.sub(r'[-_]+', '-', slugify(name)).strip('-')
    if not label:
        raise ValidationError({'name': 'Le nom du projet doit contenir au moins une lettre latine ou un chiffre.'})
    return label


def choose_project_address(organization, name):
    """Never claim an existing DNS record, even if it points to this server.

    Proposals are deterministic for this organization and name, but not
    reservations. The creation path recomputes and relies on DB uniqueness.
    """
    label = normalized_project_label(name)
    suffix = settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX
    max_label = min(63, 252 - len(suffix))
    if max_label < 10:
        raise NameCheckUnavailable()
    simple = label[:max_label].rstrip('-')
    slug_exists = Project.objects.filter(organization=organization)
    candidates = [simple]
    for attempt in range(5):
        tail = salted_hmac('autokom-project-hostname-v1',
                          f'{organization.pk}:{label}:{attempt}', algorithm='sha256').hexdigest()[:8]
        candidates.append(f'{label[:max_label - 9].rstrip("-")}-{tail}')
    for index, candidate in enumerate(candidates):
        if candidate in {'pre', 'backend'} or slug_exists.filter(slug=candidate).exists():
            continue
        hostname = validate_hostname(default_hostname(candidate))
        if Domain.objects.filter(hostname=hostname).exists():
            continue
        try:
            result = request_control('domain.available', {'hostname': hostname}, timeout=15)
        except AutokomControlUnavailable as exc:
            raise NameCheckUnavailable() from exc
        if not isinstance(result, dict) or type(result.get('available')) is not bool:
            raise NameCheckUnavailable()
        if result['available']:
            return ProjectAddress(candidate, hostname, index != 0)
    raise ValidationError({'name': 'Aucun sous-domaine disponible pour ce nom. Essayez un autre nom de projet.'})


def preview_project_address(user, organization_id, name):
    organization = get_organization(user, organization_id, write=True)
    return choose_project_address(organization, name)
