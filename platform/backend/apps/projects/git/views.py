"""API GitHub App: chaque classe hérite directement d'APIView et n'a qu'une méthode HTTP."""
import json
import base64
import binascii
import logging
from urllib.parse import urlencode, quote
from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.exceptions import ValidationError
from apps.accounts.user.permissions import IsClientUser
from helpers.responses import api_success
from helpers.errors import handle_error
from apps.projects.common import int_param, safe_relative_path
from apps.projects.git.models import GitHubAuthorization, GitConnection, GitWebhookEvent
from apps.projects.git.services import (require_github_app, authorize_url, installation_url, consume_state,
    exchange_code, save_authorization, get_installations, get_repositories, get_repository, get_branches,
    get_user_token, _request, verify_signature)
log = logging.getLogger(__name__)

from apps.projects.git.swagger.github_start import schema as github_start_schema
from apps.projects.git.swagger.github_status import schema as github_status_schema
from apps.projects.git.swagger.github_callback import schema as github_callback_schema
from apps.projects.git.swagger.github_disconnect import schema as github_disconnect_schema
from apps.projects.git.swagger.github_installations import schema as github_installations_schema
from apps.projects.git.swagger.github_repositories import schema as github_repositories_schema
from apps.projects.git.swagger.github_branches import schema as github_branches_schema
from apps.projects.git.swagger.github_directories import schema as github_directories_schema
from apps.projects.git.swagger.github_webhook import schema as github_webhook_schema


class GitHubStartAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'
    @github_start_schema
    def post(self, request):
        try:
            return api_success({'authorization_url': authorize_url(request.user),
                                'installation_url': installation_url()}, 'Autorisation GitHub préparée.')
        except Exception as exc:
            return handle_error(exc, 'github_start')


class GitHubStatusAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @github_status_schema
    def get(self, request):
        try:
            account = GitHubAuthorization.objects.filter(user=request.user, is_active=True).first()
            # Diagnostic de configuration seulement : aucun secret ni appel externe.
            configured = all((settings.GITHUB_APP_CLIENT_ID, settings.GITHUB_APP_CLIENT_SECRET,
                              settings.GITHUB_APP_CALLBACK_URL, settings.GITHUB_APP_SLUG,
                              settings.AUTOKOM_FIELD_ENCRYPTION_KEY))
            return api_success({'configured': bool(configured), 'connected': bool(account) and bool(configured),
                                'github_login': account.github_login if account and configured else None,
                                'installation_url': installation_url() if settings.GITHUB_APP_SLUG else None},
                               'État local de la connexion GitHub (sans vérification réseau).')
        except Exception as exc:
            return handle_error(exc, 'github_status')


class GitHubCallbackAPIView(APIView):
    parser_classes = [JSONParser]
    authentication_classes = []
    permission_classes = [AllowAny]
    @github_callback_schema
    def get(self, request):
        # Ne jamais journaliser la query string OAuth (code et state confidentiels).
        stage = 'state'
        try:
            user = consume_state(request.query_params.get('state', ''))
            if request.query_params.get('error'):
                log.warning('AUTOKOM GitHub repositories OAuth : autorisation refusée par le fournisseur')
                return HttpResponseRedirect(settings.FRONTEND_URL + '/integrations/github?status=denied')
            stage = 'exchange'
            data = exchange_code(request.query_params.get('code', ''))
            stage = 'identity'
            profile = _request('GET', '/user', token=data['access_token'])
            if not profile.get('id') or not profile.get('login'):
                raise ValidationError('Identité GitHub introuvable.')
            stage = 'database'
            save_authorization(user, data, profile)
            log.info('AUTOKOM GitHub repositories OAuth : connexion enregistrée')
            return HttpResponseRedirect(settings.FRONTEND_URL + '/integrations/github?status=connected')
        except Exception as exc:
            log.warning('AUTOKOM GitHub repositories OAuth : échec à l’étape %s (catégorie %s)',
                        stage, type(exc).__name__)
            return HttpResponseRedirect(settings.FRONTEND_URL + '/integrations/github?status=error')


class GitHubDisconnectAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @github_disconnect_schema
    def post(self, request):
        try:
            GitHubAuthorization.objects.filter(user=request.user).delete()
            return api_success(None, 'Connexion GitHub locale supprimée (installation GitHub non supprimée).')
        except Exception as exc:
            return handle_error(exc, 'github_disconnect')


class GitHubInstallationsAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @github_installations_schema
    def get(self, request):
        try:
            installations = [{'id': i['id'], 'account': i['account']['login'],
                'repository_selection': i['repository_selection'], 'permissions': i.get('permissions', {})}
                             for i in get_installations(request.user)]
            return api_success({'installations': installations}, 'Installations GitHub autorisées.')
        except Exception as exc:
            return handle_error(exc, 'github_installations')


class GitHubRepositoriesAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @github_repositories_schema
    def get(self, request):
        try:
            installation_id = int_param(request, 'installation_id', default=0, minimum=1, maximum=999999999999)
            page = int_param(request, 'page', default=1, maximum=10000)
            installation, result = get_repositories(request.user, installation_id, page)
            repositories = [{
                'id': repo['id'], 'installation_id': installation_id, 'name': repo['name'],
                'full_name': repo['full_name'], 'private': repo['private'],
                'default_branch': repo['default_branch'], 'html_url': repo['html_url'],
                'description': repo.get('description'),
            } for repo in result.get('repositories', [])]
            return api_success({'repositories': repositories, 'page': page,
                                'has_next': page * 100 < result.get('total_count', 0)},
                               'Dépôts autorisés par GitHub.')
        except Exception as exc:
            return handle_error(exc, 'github_repositories')


class GitHubBranchesAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @github_branches_schema
    def get(self, request):
        try:
            installation_id = int_param(request, 'installation_id', default=0, minimum=1, maximum=999999999999)
            repository_id = int_param(request, 'repository_id', default=0, minimum=1, maximum=999999999999)
            page = int_param(request, 'page', default=1, maximum=10000)
            repo, branches = get_branches(request.user, installation_id, repository_id, page)
            return api_success({'repository_id': repo['id'], 'page': page, 'branches': [
                {'name': b['name'], 'protected': b.get('protected', False),
                 'sha': (b.get('commit') or {}).get('sha', '')} for b in branches]},
                'Branches disponibles pour ce dépôt.')
        except Exception as exc:
            return handle_error(exc, 'github_branches')


class GitHubDirectoriesAPIView(APIView):
    """Parcours de dossiers en lecture seule, limité aux dépôts GitHub autorisés."""
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_browse'

    @github_directories_schema
    def get(self, request):
        try:
            installation_id = int_param(request, 'installation_id', default=0, minimum=1, maximum=999999999999)
            repository_id = int_param(request, 'repository_id', default=0, minimum=1, maximum=999999999999)
            branch = str(request.query_params.get('branch') or '').strip()
            if not branch or len(branch) > 255 or branch.startswith('-') or '..' in branch or any(c in branch for c in ('\\', '\n', '\x00')):
                raise ValidationError({'branch': 'Branche invalide.'})
            folder = str(request.query_params.get('path') or '').strip()
            if len(folder) > 300:
                raise ValidationError({'path': 'Dossier trop long.'})
            if folder:
                folder = safe_relative_path(folder)
            _, repo = get_repository(request.user, installation_id, repository_id)
            # La branche doit faire partie de celles autorisées sur ce dépôt.
            known = False
            for page in range(1, 21):
                _, branches = get_branches(request.user, installation_id, repository_id, page)
                if any(item.get('name') == branch for item in branches):
                    known = True
                    break
                if len(branches) < 100:
                    break
            if not known:
                raise ValidationError({'branch': 'Branche introuvable ou inaccessible.'})
            owner, name = repo['full_name'].split('/', 1)
            endpoint = f'/repos/{quote(owner, safe="")}/{quote(name, safe="")}/contents'
            if folder:
                endpoint += '/' + '/'.join(quote(part, safe='') for part in folder.split('/'))
            items = _request('GET', endpoint + '?ref=' + quote(branch, safe=''), token=get_user_token(request.user))
            if not isinstance(items, list):
                raise ValidationError({'path': 'Ce chemin n’est pas un dossier.'})
            directories = [{'name': item['name'], 'path': item['path'], 'type': 'dir'}
                           for item in items if item.get('type') == 'dir'
                           and isinstance(item.get('path'), str) and isinstance(item.get('name'), str)]
            # Expose only file names and allowlisted package metadata, never source code or secrets.
            files = [{'name': item['name'], 'type': 'file'} for item in items
                     if item.get('type') == 'file' and isinstance(item.get('name'), str)]
            package = None
            if any(item['name'] == 'package.json' for item in files):
                # GitHub contents endpoint; cap returned payload and avoid leaking raw file contents.
                package_response = _request('GET', endpoint + '/package.json?ref=' + quote(branch, safe=''),
                                            token=get_user_token(request.user))
                if package_response.get('encoding') == 'base64' and len(package_response.get('content', '')) <= 90000:
                    try:
                        raw = base64.b64decode(package_response['content'], validate=False)
                        if len(raw) <= 65536:
                            doc = json.loads(raw)
                            if isinstance(doc, dict) and all(isinstance(doc.get(key) or {}, dict) for key in ('dependencies','devDependencies','scripts')):
                                package = {
                                    'dependencies': {k: '*' for k in (doc.get('dependencies') or {}) if isinstance(k, str) and len(k) <= 100},
                                    'devDependencies': {k: '*' for k in (doc.get('devDependencies') or {}) if isinstance(k, str) and len(k) <= 100},
                                    'scripts': {k: 'configured' for k in (doc.get('scripts') or {}) if k in ('build', 'test', 'start', 'start:prod')},
                                }
                    except (ValueError, TypeError, KeyError, binascii.Error):
                        package = None
            return api_success({'directories': directories, 'files': files, 'package': package,
                                'path': folder}, 'Arborescence GitHub et métadonnées de build.')
        except Exception as exc:
            return handle_error(exc, 'github_directories')


class GitHubWebhookAPIView(APIView):
    parser_classes = [JSONParser]
    authentication_classes = []
    permission_classes = [AllowAny]
    @github_webhook_schema
    def post(self, request):
        try:
            raw = request.body
            if len(raw) > 1048576:
                raise ValidationError('Webhook trop volumineux.')
            verify_signature(raw, request.headers.get('X-Hub-Signature-256', ''))
            delivery_id = request.headers.get('X-GitHub-Delivery', '')
            event_type = request.headers.get('X-GitHub-Event', '')
            if not delivery_id or len(delivery_id) > 255 or not event_type or len(event_type) > 40:
                raise ValidationError('En-têtes GitHub manquants.')
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise ValidationError('Webhook JSON invalide.')
            installation_id = (payload.get('installation') or {}).get('id')
            if not isinstance(installation_id, int):
                return api_success({'accepted': True, 'recorded': 0}, 'Événement GitHub ignoré (installation absente).')
            repo = payload.get('repository') or {}
            ref = str(payload.get('ref') or '')
            recorded = 0
            from apps.deployments.runs.services import queue_verified_push
            for conn in GitConnection.objects.filter(provider='GITHUB', installation_id=installation_id, is_active=True):
                event, created = GitWebhookEvent.objects.get_or_create(connection=conn, external_event_id=delivery_id, defaults={
                    'repository_external_id': str(repo.get('id') or ''),
                    'repository_full_name': str(repo.get('full_name') or '')[:300],
                    'event_type': event_type, 'branch': ref.replace('refs/heads/', '', 1)[:255],
                    'commit_sha': str(payload.get('after') or '')[:64],
                    'signature_verified': True,
                    'status': 'RECEIVED' if event_type == 'push' else 'IGNORED',
                })
                recorded += int(created)
                # Queue only after HMAC verification; durable idempotent DB operations, no Jenkins call here.
                if event_type == 'push' and event.status == GitWebhookEvent.Status.RECEIVED:
                    queue_verified_push(event)
            return api_success({'accepted': True, 'recorded': recorded}, 'Push vérifié et synchronisation programmée si activée.')
        except Exception as exc:
            return handle_error(exc, 'github_webhook')
