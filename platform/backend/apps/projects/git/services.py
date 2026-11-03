"""Connexion GitHub App, jetons chiffrés et autorisations vérifiées auprès de GitHub."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode, quote
import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError, PermissionDenied, NotFound
from apps.projects.git.models import GitHubAuthorization, GitHubOAuthState

log = logging.getLogger(__name__)
API_BASE = 'https://api.github.com'
AUTH_BASE = 'https://github.com'
MAX_LIST_PAGES = 100


class GitHubUnavailable(APIException):
    status_code = 503
    default_detail = 'GitHub momentanément indisponible.'


class GitHubReconnectRequired(APIException):
    status_code = 409
    default_detail = 'Reconnectez votre compte GitHub.'


def require_github_app():
    if not (settings.GITHUB_APP_CLIENT_ID and settings.GITHUB_APP_CLIENT_SECRET and
            settings.GITHUB_APP_CALLBACK_URL and settings.GITHUB_APP_SLUG):
        raise APIException('L’intégration GitHub App n’est pas encore configurée.')
    if not settings.GITHUB_APP_CALLBACK_URL.startswith(('https://', 'http://127.0.0.1:', 'http://localhost:')):
        raise APIException('URL de callback GitHub non sécurisée.')


def cipher():
    key = settings.AUTOKOM_FIELD_ENCRYPTION_KEY
    if not key:
        raise APIException('Clé de chiffrement AUTOKOM manquante.')
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise APIException('Clé de chiffrement AUTOKOM invalide.')


def encrypt(value):
    return cipher().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt(value):
    try:
        return cipher().decrypt(value.encode('ascii')).decode('utf-8')
    except InvalidToken:
        raise GitHubReconnectRequired('Jeton GitHub inaccessible : reconnectez votre compte.')


def installation_url():
    import re
    slug = settings.GITHUB_APP_SLUG
    if not re.fullmatch(r'[a-zA-Z0-9-]+', slug):
        raise APIException('GITHUB_APP_SLUG invalide.')
    return f'https://github.com/apps/{slug}/installations/new'


def authorize_url(user):
    require_github_app()
    raw = secrets.token_urlsafe(40)
    GitHubOAuthState.objects.create(user=user, state_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                   expires_at=timezone.now() + timedelta(minutes=10))
    query = urlencode({'client_id': settings.GITHUB_APP_CLIENT_ID,
                       'redirect_uri': settings.GITHUB_APP_CALLBACK_URL, 'state': raw})
    return f'{AUTH_BASE}/login/oauth/authorize?{query}'


def consume_state(raw):
    if not raw or len(raw) > 200:
        raise ValidationError({'state': 'État GitHub invalide.'})
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with transaction.atomic():
        state = GitHubOAuthState.objects.select_for_update().filter(state_hash=digest).first()
        if not state or state.consumed_at or state.expires_at <= timezone.now():
            raise ValidationError({'state': 'Autorisation expirée ou déjà consommée.'})
        state.consumed_at = timezone.now()
        state.save(update_fields=['consumed_at'])
        return state.user


def _request(method, endpoint, token=None, *, payload=None, github_login=False):
    base = AUTH_BASE if github_login else API_BASE
    headers = {'Accept': 'application/json' if github_login else 'application/vnd.github+json',
               'User-Agent': 'AUTOKOM-Platform/1.0'}
    if not github_login:
        headers['X-GitHub-Api-Version'] = settings.GITHUB_API_VERSION
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        response = requests.request(method, base + endpoint, headers=headers, json=None if github_login else payload,
                                    data=payload if github_login else None, timeout=(5, 15), allow_redirects=False)
    except requests.RequestException:
        log.exception('GitHub réseau indisponible')
        raise GitHubUnavailable()
    if response.status_code in (401,):
        raise GitHubReconnectRequired()
    if response.status_code in (403, 404):
        raise PermissionDenied('Dépôt inaccessible ou autorisation GitHub insuffisante.')
    if response.status_code == 429 or response.status_code >= 500:
        raise GitHubUnavailable()
    if response.status_code >= 400:
        raise ValidationError({'github': 'GitHub a refusé la requête.'})
    try:
        return response.json()
    except ValueError:
        raise GitHubUnavailable('Réponse GitHub non valide.')


def exchange_code(code):
    require_github_app()
    if not code or len(code) > 1024:
        raise ValidationError({'code': 'Code OAuth manquant ou invalide.'})
    result = _request('POST', '/login/oauth/access_token', payload={
        'client_id': settings.GITHUB_APP_CLIENT_ID, 'client_secret': settings.GITHUB_APP_CLIENT_SECRET,
        'code': code, 'redirect_uri': settings.GITHUB_APP_CALLBACK_URL,
    }, github_login=True)
    if not result.get('access_token'):
        raise GitHubReconnectRequired('GitHub n’a pas délivré de jeton.')
    return result


def save_authorization(user, data, github_user):
    now = timezone.now()
    GitHubAuthorization.objects.update_or_create(user=user, defaults={
        'github_user_id': github_user['id'], 'github_login': github_user['login'],
        'access_token_ciphertext': encrypt(data['access_token']),
        'refresh_token_ciphertext': encrypt(data['refresh_token']) if data.get('refresh_token') else '',
        'access_expires_at': now + timedelta(seconds=int(data['expires_in'])) if data.get('expires_in') else None,
        'refresh_expires_at': now + timedelta(seconds=int(data['refresh_token_expires_in'])) if data.get('refresh_token_expires_in') else None,
        'is_active': True,
    })


def get_user_token(user):
    with transaction.atomic():
        auth = GitHubAuthorization.objects.select_for_update().filter(user=user, is_active=True).first()
        if not auth:
            raise GitHubReconnectRequired()
        if auth.access_expires_at and auth.access_expires_at <= timezone.now() + timedelta(minutes=2):
            if not auth.refresh_token_ciphertext or (auth.refresh_expires_at and auth.refresh_expires_at <= timezone.now()):
                raise GitHubReconnectRequired()
            data = _request('POST', '/login/oauth/access_token', payload={
                'client_id': settings.GITHUB_APP_CLIENT_ID,
                'client_secret': settings.GITHUB_APP_CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': decrypt(auth.refresh_token_ciphertext),
            }, github_login=True)
            if not data.get('access_token'):
                raise GitHubReconnectRequired()
            auth.access_token_ciphertext = encrypt(data['access_token'])
            auth.refresh_token_ciphertext = encrypt(data.get('refresh_token', '')) if data.get('refresh_token') else ''
            auth.access_expires_at = timezone.now() + timedelta(seconds=int(data['expires_in'])) if data.get('expires_in') else None
            auth.refresh_expires_at = timezone.now() + timedelta(seconds=int(data['refresh_token_expires_in'])) if data.get('refresh_token_expires_in') else None
            auth.save(update_fields=['access_token_ciphertext', 'refresh_token_ciphertext', 'access_expires_at', 'refresh_expires_at', 'updated_at'])
        return decrypt(auth.access_token_ciphertext)


def get_installations(user):
    token = get_user_token(user)
    results = []
    for page in range(1, MAX_LIST_PAGES + 1):
        data = _request('GET', f'/user/installations?per_page=100&page={page}', token=token)
        batch = data.get('installations', [])
        results.extend(batch)
        if len(batch) < 100:
            break
    return results


def get_installation(user, installation_id):
    try:
        installation_id = int(installation_id)
    except (TypeError, ValueError):
        raise ValidationError({'installation_id': 'Identifiant numérique attendu.'})
    installation = next((x for x in get_installations(user) if x.get('id') == installation_id), None)
    if not installation:
        raise PermissionDenied('Installation GitHub non autorisée pour ce compte.')
    if installation.get('suspended_at'):
        raise PermissionDenied('Installation GitHub suspendue.')
    return installation


def get_repositories(user, installation_id, page=1):
    installation = get_installation(user, installation_id)
    data = _request('GET', f'/user/installations/{int(installation_id)}/repositories?per_page=100&page={int(page)}',
                    token=get_user_token(user))
    return installation, data


def get_repository(user, installation_id, repository_id):
    """Verify the selected repository against GitHub, not a database cache or URL supplied by a client."""
    if not str(repository_id).isdigit():
        raise ValidationError({'repository_id': 'Identifiant numérique attendu.'})
    installation = get_installation(user, installation_id)
    for page in range(1, MAX_LIST_PAGES + 1):
        _, data = get_repositories(user, installation_id, page)
        for repo in data.get('repositories', []):
            if int(repo['id']) == int(repository_id):
                return installation, repo
        if len(data.get('repositories', [])) < 100:
            break
    raise PermissionDenied('Ce dépôt n’est pas autorisé dans l’installation GitHub.')


def get_branches(user, installation_id, repository_id, page=1):
    installation, repo = get_repository(user, installation_id, repository_id)
    full = repo['full_name'].split('/')
    if len(full) != 2:
        raise ValidationError({'repository': 'Nom GitHub invalide.'})
    owner, name = quote(full[0], safe=''), quote(full[1], safe='')
    branches = _request('GET', f'/repos/{owner}/{name}/branches?per_page=100&page={int(page)}', token=get_user_token(user))
    return repo, branches


def verify_signature(raw, signature):
    secret = settings.GITHUB_WEBHOOK_SECRET
    if not secret:
        raise GitHubUnavailable('Webhook GitHub non configuré.')
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ''):
        raise PermissionDenied('Signature webhook invalide.')
