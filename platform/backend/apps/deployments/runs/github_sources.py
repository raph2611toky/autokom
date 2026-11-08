"""Per-run, repository-scoped GitHub archive download for isolated Jenkins agents.

GitHub App user tokens stay exclusively on Django. Jenkins receives only a
bounded snapshot of the pinned commit over the existing HMAC-authenticated API.
This deliberately does not provision a global, multi-tenant Jenkins credential.
"""
from __future__ import annotations

import re
import tempfile
from urllib.parse import quote, urlsplit

import requests
from rest_framework.exceptions import APIException, PermissionDenied

from apps.deployments.runs.models import DeploymentRun
from apps.projects.git.services import get_repository, get_user_token

SHA = re.compile(r'^[a-f0-9]{40}$')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


class SourceUnavailable(APIException):
    status_code = 502
    default_detail = 'GitHub ne permet pas actuellement de récupérer le commit demandé.'


def authorized_source(run_id):
    """Resolve the *persisted* project and verify its exact installation/repo id.

    Never trust repository names or installations submitted by Jenkins or the
    browser. Webhooks use the member who originally linked the GitHub App.
    """
    run = (DeploymentRun.objects.select_related(
        'requested_by', 'environment__project__git_connection__created_by')
        .filter(pk=run_id, status=DeploymentRun.Status.RUNNING, cancel_requested=False).first())
    if run is None:
        raise PermissionDenied('Exécution source indisponible.')
    project = run.environment.project
    connection = project.git_connection
    if (connection is None or not connection.is_active or connection.provider != 'GITHUB'
            or connection.organization_id != project.organization_id or not connection.installation_id
            or not REPO.fullmatch(project.repository_name or '') or not SHA.fullmatch(run.commit_sha or '')
            or not str(project.repository_external_id).isdigit()):
        raise PermissionDenied('Source GitHub non autorisée pour ce déploiement.')
    actor = run.requested_by or connection.created_by
    _, repository = get_repository(actor, connection.installation_id, project.repository_external_id)
    if (str(repository.get('id')) != str(project.repository_external_id)
            or str(repository.get('full_name', '')).lower() != project.repository_name.lower()):
        raise PermissionDenied('La source GitHub ne correspond plus au dépôt autorisé.')
    return project.repository_name, run.commit_sha, get_user_token(actor)


def _github_archive_url(repository, commit_sha):
    owner, name = repository.split('/')
    return f'https://api.github.com/repos/{quote(owner, safe="")}/{quote(name, safe="")}/zipball/{commit_sha}'


def download_source_archive(run_id, *, request=requests.get):
    """Return a bounded, seekable archive; caller transfers/owns and closes it.

    The API token is sent ONLY to api.github.com, never to redirects or the
    Jenkins agent. Follow only a GitHub-controlled codeload HTTPS destination.
    """
    repository, sha, token = authorized_source(run_id)
    api_url = _github_archive_url(repository, sha)
    destination = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    try:
        with request(api_url, headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'AUTOKOM-Platform/1.0',
            }, timeout=(5, 30), allow_redirects=False, stream=True) as first:
            if first.status_code in (401, 403, 404):
                raise PermissionDenied('GitHub App : vérifier la connexion du compte et l’autorisation Contents: read du dépôt.')
            if first.status_code != 302:
                raise SourceUnavailable()
            location = first.headers.get('Location', '')
        url = urlsplit(location)
        owner, name = repository.split('/')
        if (url.scheme != 'https' or url.hostname != 'codeload.github.com'
                or url.port not in (None, 443) or url.username or url.password
                or not url.path.lower().startswith(f'/{owner.lower()}/{name.lower()}/')
                or url.fragment):
            raise SourceUnavailable('Redirection GitHub non autorisée.')
        # Important: no Authorization header on the signed/temporary codeload URL.
        with request(location, headers={'User-Agent': 'AUTOKOM-Platform/1.0'},
                     timeout=(5, 60), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise SourceUnavailable()
            size = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise SourceUnavailable('Archive GitHub trop volumineuse pour ce plan.')
                destination.write(chunk)
            if size < 30:
                raise SourceUnavailable('Archive GitHub vide.')
        destination.seek(0)
        return destination
    except requests.RequestException as exc:
        destination.close()
        raise SourceUnavailable('Connexion GitHub momentanément indisponible.') from exc
    except Exception:
        destination.close()
        raise
