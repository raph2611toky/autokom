"""Browser auth for the same-origin AUTOKOM SPA.

Legacy `/api/auth/login/`, `/token/refresh/`, GitHub exchange, and Bearer clients
are intentionally unchanged. Here the browser NEVER receives raw JWTs in JSON.
"""
from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.exceptions import AuthenticationFailed
from helpers.responses import api_success, api_error
from apps.accounts.user.models import User
from apps.accounts.user.serializers import LoginSerializer, UserSerializer, GitHubExchangeSerializer
from apps.accounts.user.github_auth_service import (
    consume_login_ticket, consume_oauth_state, verified_identity, finish_oauth,
)
from apps.accounts.user.tokens import (
    issue_pair, rotate_refresh, decode_token, validate_active_session,
)

ACCESS_COOKIE = 'autokom_access'
REFRESH_COOKIE = 'autokom_refresh'
ACCESS_PATH = '/api/'
REFRESH_PATH = '/api/auth/browser/'


def _uncache(response):
    response['Cache-Control'] = 'no-store, private'
    response['Pragma'] = 'no-cache'
    return response


def _set_session_cookies(response, pair):
    flags = {'httponly': True, 'secure': settings.CLIENT_BROWSER_COOKIE_SECURE,
             'samesite': 'Lax'}
    response.set_cookie(ACCESS_COOKIE, pair['access'], path=ACCESS_PATH,
                        max_age=int(settings.CLIENT_ACCESS_LIFETIME.total_seconds()), **flags)
    response.set_cookie(REFRESH_COOKIE, pair['refresh'], path=REFRESH_PATH,
                        max_age=int(settings.CLIENT_REFRESH_LIFETIME.total_seconds()), **flags)
    return _uncache(response)


def _clear_session_cookies(response):
    response.delete_cookie(ACCESS_COOKIE, path=ACCESS_PATH, samesite='Lax')
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, samesite='Lax')
    return _uncache(response)


def _session_result(request, user, pair, message):
    result = api_success({'user': UserSerializer(user, context={'request': request}).data,
                          'access_expires_in': int(settings.CLIENT_ACCESS_LIFETIME.total_seconds())}, message)
    return _set_session_cookies(result, pair)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class BrowserCSRFAPIView(APIView):
    """Call before browser login and before any unsafe cookie-authenticated API call."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return _uncache(api_success(message='CSRF prêt.'))


@method_decorator(csrf_protect, name='dispatch')
class BrowserLoginAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data['email'].strip()).first()
        if not user or not user.check_password(serializer.validated_data['password']):
            return _uncache(api_error('Identifiants invalides.', status_code=401))
        if not user.is_active or not user.is_email_verified:
            return _uncache(api_error('Compte inactif ou e-mail non vérifié.', status_code=403))
        pair = issue_pair(user, ip_address=request.META.get('REMOTE_ADDR') or None,
                          user_agent=request.headers.get('User-Agent', ''))
        User.objects.filter(pk=user.pk).update(last_login_at=timezone.now())
        return _session_result(request, user, pair, 'Connexion réussie.')


@method_decorator(csrf_protect, name='dispatch')
class BrowserRefreshAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh'

    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE, '')
        if not raw:
            raise AuthenticationFailed('Session expirée : reconnectez-vous.')
        pair = rotate_refresh(raw, ip_address=request.META.get('REMOTE_ADDR') or None,
                              user_agent=request.headers.get('User-Agent', ''))
        user, _ = decode_token(pair['access'], expected_kind='access')
        return _session_result(request, user, pair, 'Session renouvelée.')


@method_decorator(csrf_protect, name='dispatch')
class BrowserLogoutAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        # No JSON/body refresh token. Revoke current session when it is valid;
        # clear the browser cookies even when the session has expired.
        raw = request.COOKIES.get(REFRESH_COOKIE, '')
        if raw:
            try:
                user, claims = decode_token(raw, expected_kind='refresh')
                session = validate_active_session(user, claims, check_refresh_digest=True)
                session.revoked_at = timezone.now()
                session.save(update_fields=['revoked_at', 'updated_at'])
            except (AuthenticationFailed, ValueError, TypeError):
                pass
        return _clear_session_cookies(api_success(message='Session déconnectée.'))


@method_decorator(csrf_protect, name='dispatch')
class BrowserGitHubExchangeAPIView(APIView):
    """Optional GitHub callback exchange using cookies, not bearer tokens in JS."""
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'

    def post(self, request):
        serializer = GitHubExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = consume_login_ticket(serializer.validated_data['ticket'],
                                    serializer.validated_data['browser_binding'])
        pair = issue_pair(user, ip_address=request.META.get('REMOTE_ADDR') or None,
                          user_agent=request.headers.get('User-Agent', ''))
        User.objects.filter(pk=user.pk).update(last_login_at=timezone.now())
        return _session_result(request, user, pair, 'Connexion GitHub réussie.')


@method_decorator(csrf_protect, name='dispatch')
class BrowserGitHubCompleteAPIView(APIView):
    """Callback OAuth reçu par React, code échangé côté serveur uniquement.

    POST même origine + CSRF + state/PKCE liés au navigateur. Réponse : user et
    cookies HttpOnly (aucun code OAuth, jeton GitHub ou JWT dans le JSON).
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'

    def post(self, request):
        from rest_framework.exceptions import ValidationError
        if not isinstance(request.data, dict):
            raise ValidationError({'github': 'Format de requête invalide.'})
        code = request.data.get('code')
        state = request.data.get('state')
        binding = request.data.get('browser_binding')
        if (not isinstance(code, str) or not 10 <= len(code) <= 1024 or
            not isinstance(state, str) or not 30 <= len(state) <= 160 or
            not isinstance(binding, str) or not 40 <= len(binding) <= 256):
            raise ValidationError({'github': 'Réponse GitHub ou session navigateur invalide.'})
        # Ne jamais écrire code/state/binding dans les logs applicatifs.
        intent, verifier, link_user, binding_hash = consume_oauth_state(
            state, browser_binding=binding,
        )
        github_id, login, email, name = verified_identity(code, verifier)
        status, ticket = finish_oauth(
            intent=intent, link_user=link_user, github_id=github_id,
            github_login=login, email=email, full_name=name,
            browser_binding_hash=binding_hash,
        )
        if status not in ('ok', 'created'):
            return _uncache(api_success({'status': status}, 'Réponse GitHub vérifiée.'))
        user = consume_login_ticket(ticket, binding)
        pair = issue_pair(user, ip_address=request.META.get('REMOTE_ADDR') or None,
                          user_agent=request.headers.get('User-Agent', ''))
        User.objects.filter(pk=user.pk).update(last_login_at=timezone.now())
        response = _session_result(request, user, pair, 'Authentification GitHub réussie.')
        # Statut conservé pour l'animation du frontend.
        response.data['data']['status'] = status
        return response
