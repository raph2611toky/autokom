"""Quatre APIView, une seule méthode par classe, Swagger distinct par endpoint."""
from urllib.parse import urlencode
import logging
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from helpers.responses import api_success
from helpers.errors import handle_error
from apps.accounts.user.permissions import IsClientUser
from apps.accounts.user.models import User
from apps.accounts.user.serializers import UserSerializer, GitHubExchangeSerializer
from apps.accounts.user.tokens import issue_pair
log = logging.getLogger(__name__)

from apps.accounts.user.github_auth_service import (
    start_oauth, consume_oauth_state, verified_identity, finish_oauth, consume_login_ticket,
)
from apps.accounts.user.swagger.github_login_start import schema as github_login_start_swagger
from apps.accounts.user.swagger.github_link_start import schema as github_link_start_swagger
from apps.accounts.user.swagger.github_login_callback import schema as github_login_callback_swagger
from apps.accounts.user.swagger.github_login_exchange import schema as github_login_exchange_swagger


class GitHubLoginStartAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'

    @github_login_start_swagger
    def get(self, request):
        try:
            intent = request.query_params.get('intent', 'login')
            url, binding = start_oauth(intent)
            response = api_success({'authorization_url': url, 'browser_binding': binding}, 'Autorisation GitHub prête.')
            response['Cache-Control'] = 'no-store, private'
            return response
        except Exception as exc:
            return handle_error(exc, 'github_login_start')


class GitHubLinkStartAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'

    @github_link_start_swagger
    def post(self, request):
        try:
            url, binding = start_oauth('link', link_user=request.user)
            response = api_success({'authorization_url': url, 'browser_binding': binding}, 'Liaison GitHub prête.')
            response['Cache-Control'] = 'no-store, private'
            return response
        except Exception as exc:
            return handle_error(exc, 'github_link_start')


class GitHubLoginCallbackAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'

    @github_login_callback_swagger
    def get(self, request):
        # Codes/tokens OAuth jamais présents dans la redirection finale.
        status, ticket = 'error', None
        stage = 'state'
        try:
            intent, verifier, link_user, browser_binding_hash = consume_oauth_state(request.query_params.get('state', ''))
            if request.query_params.get('error'):
                status = 'denied'
                log.warning('AUTOKOM GitHub account OAuth : autorisation refusée par le fournisseur')
            else:
                stage = 'identity'
                github_id, login, email, name = verified_identity(request.query_params.get('code', ''), verifier, legacy_callback=True)
                stage = 'database'
                status, ticket = finish_oauth(intent=intent, link_user=link_user,
                    github_id=github_id, github_login=login, email=email, full_name=name,
                    browser_binding_hash=browser_binding_hash)
                log.info('AUTOKOM GitHub account OAuth : résultat %s', status)
        except Exception as exc:
            # Ne jamais écrire code, state, ticket, e-mail ou les jetons dans les journaux.
            log.warning('AUTOKOM GitHub account OAuth : échec à l’étape %s (catégorie %s)',
                        stage, type(exc).__name__)
            status = 'error'
        fragment = urlencode({'status': status, **({'ticket': ticket} if ticket else {})})
        response = HttpResponseRedirect(settings.FRONTEND_URL + '/auth/github/callback#' + fragment)
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class GitHubLoginExchangeAPIView(APIView):
    parser_classes = [JSONParser]
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'github_oauth'

    @github_login_exchange_swagger
    def post(self, request):
        try:
            serializer = GitHubExchangeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = consume_login_ticket(serializer.validated_data['ticket'],
                                        serializer.validated_data['browser_binding'])
            tokens = issue_pair(user, ip_address=request.META.get('REMOTE_ADDR') or None,
                                user_agent=request.headers.get('User-Agent', ''))
            User.objects.filter(pk=user.pk).update(last_login_at=timezone.now())
            response = api_success({'tokens': tokens, 'user': UserSerializer(user, context={'request': request}).data},
                                   'Connexion GitHub réussie.')
            response['Cache-Control'] = 'no-store, private'
            return response
        except Exception as exc:
            return handle_error(exc, 'github_login_exchange')
