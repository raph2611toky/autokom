"""OAuth GitHub App pour les clients, sans réutiliser le callback des dépôts.

State et PKCE à usage unique; seul l'ID GitHub est lié au compte. Aucun jeton
GitHub n'est enregistré ici ni envoyé au frontend.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import hmac
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError
from helpers.secure_actions import enc_dec
from helpers.security import token_digest
from apps.accounts.user.models import User
from apps.accounts.user.github_auth_models import (
    ClientGitHubIdentity, ClientGitHubOAuthState, ClientGitHubLoginTicket,
)
from apps.accounts.user.services import register_client


class GitHubAuthUnavailable(APIException):
    status_code = 503
    default_detail = 'Connexion GitHub temporairement indisponible.'


class GitHubAuthConflict(APIException):
    status_code = 409
    default_detail = 'Compte existant : connectez-vous avec votre mot de passe AUTOKOM puis associez GitHub.'


def auth_callback_url() -> str:
    """GitHub revient directement sur la SPA, jamais sur le backend pour l'auth client.

    Compatibilité avec backend.env existant : l'ancien callback backend est
    converti vers l'origine FRONTEND_URL. L'installation des dépôts continue
    d'utiliser GITHUB_APP_CALLBACK_URL et n'est pas modifiée.
    """
    from urllib.parse import urlsplit, urlunsplit
    frontend = urlsplit(settings.FRONTEND_URL)
    configured = urlsplit(getattr(settings, 'GITHUB_AUTH_CALLBACK_URL', '').strip())
    if configured.scheme and configured.path not in ('/api/auth/github/callback/',
                                                     '/auth/github/callback'):
        raise ValidationError({'github': 'Chemin du callback GitHub non autorisé.'})
    if configured.scheme and configured.path == '/auth/github/callback':
        if (configured.scheme, configured.netloc) != (frontend.scheme, frontend.netloc):
            raise ValidationError({'github': 'Callback client différent de FRONTEND_URL.'})
    elif configured.scheme and configured.path == '/api/auth/github/callback/':
        project_host = urlsplit(settings.GITHUB_APP_CALLBACK_URL)
        if (configured.scheme, configured.netloc) != (project_host.scheme, project_host.netloc):
            raise ValidationError({'github': 'Ancien callback backend inattendu.'})
    elif configured.scheme or getattr(settings, 'GITHUB_AUTH_CALLBACK_URL', '').strip():
        raise ValidationError({'github': 'Callback GitHub invalide.'})
    if (not frontend.hostname or frontend.username or frontend.password or frontend.port not in (None, 443, 5173)
            or frontend.path not in ('', '/') or frontend.query or frontend.fragment
            or not (frontend.scheme == 'https' or
                    (frontend.scheme == 'http' and frontend.hostname in ('localhost', '127.0.0.1')))):
        raise ValidationError({'github': 'FRONTEND_URL doit être une origine HTTPS valide.'})
    if configured.username or configured.password or configured.query or configured.fragment:
        raise ValidationError({'github': 'Callback GitHub invalide (query/fragment interdits).'})
    return urlunsplit((frontend.scheme, frontend.netloc, '/auth/github/callback', '', ''))


def _require_config():
    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        raise GitHubAuthUnavailable('GitHub App non configurée.')
    auth_callback_url()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def start_oauth(intent: str, *, link_user=None) -> tuple[str, str]:
    _require_config()
    if intent not in ('login', 'signup', 'link') or (intent == 'link') != (link_user is not None):
        raise ValidationError({'intent': 'Mode OAuth invalide.'})
    raw_state = secrets.token_urlsafe(40)
    browser_binding = secrets.token_urlsafe(48)  # Secret navigateur, JAMAIS dans l'URL GitHub.
    verifier = secrets.token_urlsafe(48)
    ClientGitHubOAuthState.objects.create(
        state_hash=token_digest(raw_state), browser_binding_hash=token_digest(browser_binding),
        intent=intent, link_user=link_user,
        pkce_verifier_ciphertext=enc_dec(verifier), expires_at=timezone.now() + timedelta(minutes=10),
    )
    params = {
        'client_id': settings.GITHUB_APP_CLIENT_ID, 'redirect_uri': auth_callback_url(),
        'state': raw_state, 'code_challenge': _challenge(verifier), 'code_challenge_method': 'S256',
        'prompt': 'select_account',
    }
    return 'https://github.com/login/oauth/authorize?' + urlencode(params), browser_binding


def consume_oauth_state(raw: str, *, browser_binding: str | None = None) -> tuple[str, str, User | None, str]:
    if not raw or len(raw) > 160:
        raise ValidationError({'state': 'Session GitHub expirée.'})
    with transaction.atomic():
        state = ClientGitHubOAuthState.objects.select_for_update().select_related('link_user').filter(
            state_hash=token_digest(raw), consumed_at__isnull=True, expires_at__gt=timezone.now(),
        ).first()
        if not state:
            raise ValidationError({'state': 'Session GitHub expirée ou déjà utilisée.'})
        # La SPA doit présenter la preuve de possession du navigateur original.
        # Contrôle avant consommation : une requête forgée ne peut pas brûler le state.
        if browser_binding is not None and (
            len(browser_binding) < 40 or len(browser_binding) > 256 or
            not hmac.compare_digest(state.browser_binding_hash, token_digest(browser_binding))
        ):
            raise ValidationError({'state': 'Navigateur OAuth non reconnu.'})
        state.consumed_at = timezone.now()
        state.save(update_fields=['consumed_at'])
        return state.intent, enc_dec(state.pkce_verifier_ciphertext, 'd'), state.link_user, state.browser_binding_hash


def _github_request(method: str, url: str, *, token: str = '', payload: dict | None = None):
    headers = {'User-Agent': 'AUTOKOM-Platform/1.0', 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': settings.GITHUB_API_VERSION}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        if method == 'POST':
            response = requests.post(url, data=payload, headers={**headers, 'Accept': 'application/json'},
                                     timeout=(5, 15), allow_redirects=False)
        else:
            response = requests.get(url, headers=headers, timeout=(5, 15), allow_redirects=False)
    except requests.RequestException as exc:
        raise GitHubAuthUnavailable() from exc
    if response.status_code >= 400 or response.status_code < 200 or response.status_code >= 300:
        raise GitHubAuthUnavailable('GitHub n’a pas validé cette autorisation.')
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubAuthUnavailable('Réponse GitHub incorrecte.') from exc


def verified_identity(code: str, verifier: str, *, legacy_callback: bool = False) -> tuple[int, str, str, str]:
    _require_config()
    if not code or len(code) > 1024:
        raise ValidationError({'github': 'Code OAuth invalide.'})
    # Un flux démarré AVANT la migration peut revenir sur l'ancien endpoint
    # backend. Seule cette vue serveur peut sélectionner l'ancienne URI.
    if legacy_callback:
        from urllib.parse import urlsplit, urlunsplit
        original = urlsplit(settings.GITHUB_APP_CALLBACK_URL)
        redirect_uri = urlunsplit((original.scheme, original.netloc, '/api/auth/github/callback/', '', ''))
    else:
        redirect_uri = auth_callback_url()
    exchange = _github_request('POST', 'https://github.com/login/oauth/access_token', payload={
        'client_id': settings.GITHUB_APP_CLIENT_ID,
        'client_secret': settings.GITHUB_APP_CLIENT_SECRET,
        'code': code, 'redirect_uri': redirect_uri, 'code_verifier': verifier,
    })
    token = exchange.get('access_token')
    if not token:
        raise GitHubAuthUnavailable('Autorisation GitHub refusée.')
    profile = _github_request('GET', 'https://api.github.com/user', token=token)
    emails = _github_request('GET', 'https://api.github.com/user/emails', token=token)
    primary = next((item for item in emails if isinstance(item, dict) and item.get('primary') is True and
                    item.get('verified') is True and item.get('email')), None) if isinstance(emails, list) else None
    if not primary:
        raise ValidationError({'github': 'Autoriser la lecture de votre e-mail principal vérifié sur GitHub.'})
    try:
        github_id = int(profile['id'])
        login = str(profile['login'])[:160]
        email = str(primary['email']).strip().lower()
        name = str(profile.get('name') or login).strip()[:180]
        if github_id < 1 or not login or '@' not in email or not name:
            raise ValueError()
    except (TypeError, ValueError, KeyError) as exc:
        raise ValidationError({'github': 'Identité GitHub incomplète.'}) from exc
    return github_id, login, email, name


def _make_ticket(user: User, browser_binding_hash: str) -> str:
    raw = secrets.token_urlsafe(48)
    ClientGitHubLoginTicket.objects.create(user=user, token_hash=token_digest(raw),
        browser_binding_hash=browser_binding_hash,
        expires_at=timezone.now() + timedelta(seconds=90))
    return raw


def finish_oauth(*, intent: str, link_user: User | None, github_id: int, github_login: str,
                 email: str, full_name: str, browser_binding_hash: str) -> tuple[str, str | None]:
    """Retourne (statut, ticket éventuel). Ne fusionne jamais les comptes par e-mail."""
    with transaction.atomic():
        identity = ClientGitHubIdentity.objects.select_for_update().select_related('user').filter(
            github_user_id=github_id).first()
        if intent == 'link':
            if not link_user or not link_user.is_active or not link_user.is_email_verified:
                return 'denied', None
            if identity and identity.user_id != link_user.pk:
                return 'already_linked', None
            other = ClientGitHubIdentity.objects.select_for_update().filter(user=link_user).first()
            if other and other.github_user_id != github_id:
                return 'already_linked', None
            if not identity:
                ClientGitHubIdentity.objects.create(user=link_user, github_user_id=github_id,
                                                    github_login=github_login)
            else:
                identity.github_login = github_login
                identity.save(update_fields=['github_login', 'updated_at'])
            return 'linked', None

        if identity:
            user = identity.user
            if not user.is_active or not user.is_email_verified:
                return 'denied', None
            if identity.github_login != github_login:
                identity.github_login = github_login
                identity.save(update_fields=['github_login', 'updated_at'])
            return 'ok', _make_ticket(user, browser_binding_hash)

        # Une identité GitHub non associée ne peut PAS prendre possession d'un compte
        # local juste parce que l'adresse e-mail est identique.
        if User.objects.filter(email__iexact=email).exists():
            return 'account_exists', None
        # Se connecter avec GitHub ou s'inscrire avec GitHub : provisionnement
        # idempotent du compte si cet ID GitHub est nouveau et l'e-mail est libre.
        # Un compte local existant n'est JAMAIS lié par simple correspondance d'e-mail.
        # Le GitHub App /user/emails a certifié l'e-mail principal. Mot de passe
        # imprévisible et inconnu de l'utilisateur; récupération par e-mail possible.
        user = register_client(email=email, full_name=full_name, password=secrets.token_urlsafe(64))
        user.is_email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=['is_email_verified', 'email_verified_at', 'updated_at'])
        ClientGitHubIdentity.objects.create(user=user, github_user_id=github_id, github_login=github_login)
        return 'created', _make_ticket(user, browser_binding_hash)


def consume_login_ticket(raw: str, browser_binding: str) -> User:
    if (not raw or len(raw) > 256 or not browser_binding or len(browser_binding) > 256):
        raise ValidationError({'ticket': 'Ticket de connexion invalide.'})
    with transaction.atomic():
        ticket = ClientGitHubLoginTicket.objects.select_for_update().select_related('user').filter(
            token_hash=token_digest(raw), consumed_at__isnull=True, expires_at__gt=timezone.now()).first()
        if (not ticket or not ticket.user.is_active or not ticket.user.is_email_verified or
                not hmac.compare_digest(ticket.browser_binding_hash, token_digest(browser_binding))):
            raise ValidationError({'ticket': 'Ticket de connexion invalide ou expiré.'})
        ticket.consumed_at = timezone.now()
        ticket.save(update_fields=['consumed_at'])
        return ticket.user
