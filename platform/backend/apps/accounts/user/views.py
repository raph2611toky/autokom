"""API des clients: exactement une méthode HTTP par APIView, pas de ViewSet."""
import logging
from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from helpers.responses import api_success, api_error
from helpers.errors import handle_error
from helpers.security import token_digest
from apps.organizations.models import OrganizationMembership
from apps.projects.project.models import ProjectMember
from apps.accounts.user.models import User, UserActionToken, UserSession
from apps.accounts.user.permissions import IsClientUser
from apps.accounts.user.serializers import (UserSerializer, RegisterSerializer, TokenSerializer, EmailSerializer,
    LoginSerializer, RefreshSerializer, LogoutSerializer, ResetPasswordSerializer,
    ChangePasswordSerializer, MePatchSerializer, MeAvatarSerializer, SessionSerializer)
from apps.accounts.user.services import register_client, send_verification, send_reset
from apps.accounts.user.tokens import issue_pair, decode_token, validate_active_session, rotate_refresh, revoke_all_user_sessions
from apps.accounts.user.swagger.register import schema as register_swagger
from apps.accounts.user.swagger.email_verify import schema as email_verify_swagger
from apps.accounts.user.swagger.email_resend import schema as email_resend_swagger
from apps.accounts.user.swagger.login import schema as login_swagger
from apps.accounts.user.swagger.refresh import schema as refresh_swagger
from apps.accounts.user.swagger.logout import schema as logout_swagger
from apps.accounts.user.swagger.logout_all import schema as logout_all_swagger
from apps.accounts.user.swagger.forgot_password import schema as forgot_password_swagger
from apps.accounts.user.swagger.reset_password import schema as reset_password_swagger
from apps.accounts.user.swagger.change_password import schema as change_password_swagger
from apps.accounts.user.swagger.me_get import schema as me_get_swagger
from apps.accounts.user.swagger.me_patch import schema as me_patch_swagger
from apps.accounts.user.swagger.me_avatar import schema as me_avatar_swagger
from apps.accounts.user.swagger.sessions_list import schema as sessions_list_swagger
from apps.accounts.user.swagger.sessions_revoke import schema as sessions_revoke_swagger
from apps.accounts.user.swagger.permissions_get import schema as permissions_get_swagger

logger = logging.getLogger(__name__)

class RegistrationAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'registration'

    @register_swagger
    def post(self, request):
        try:
            serializer = RegisterSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            user = register_client(email=data['email'], full_name=data['full_name'], password=data['password'])
            mail_sent = send_verification(user)
            return api_success({'user': UserSerializer(user, context={'request': request}).data,
                'verification_email_sent': mail_sent}, 'Compte créé. Vérifiez votre adresse e-mail.', 201)
        except Exception as exc:
            return handle_error(exc, 'register')

class VerifyEmailAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    @email_verify_swagger
    def post(self, request):
        try:
            serializer = TokenSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            with transaction.atomic():
                verification = UserActionToken.objects.select_for_update().select_related('user').filter(
                    purpose=UserActionToken.Purpose.VERIFY_EMAIL, token_hash=token_digest(serializer.validated_data['token'])).first()
                if not verification or verification.consumed_at or verification.expires_at <= timezone.now():
                    return api_error('Lien de vérification invalide ou expiré.', status_code=400)
                user = verification.user
                verification.consumed_at = timezone.now()
                verification.save(update_fields=['consumed_at', 'updated_at'])
                User.objects.filter(pk=user.pk).update(is_email_verified=True, email_verified_at=timezone.now())
            return api_success(message='Adresse e-mail vérifiée.')
        except Exception as exc:
            return handle_error(exc, 'email_verify')

class ResendEmailAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'registration'

    @email_resend_swagger
    def post(self, request):
        try:
            serializer = EmailSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = User.objects.filter(email__iexact=serializer.validated_data['email'].strip()).first()
            if user and user.is_active and not user.is_email_verified:
                send_verification(user)
            return api_success(message='Si le compte existe et nécessite une vérification, un message a été envoyé.')
        except Exception as exc:
            return handle_error(exc, 'email_resend')

class LoginAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    @login_swagger
    def post(self, request):
        try:
            serializer = LoginSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = User.objects.filter(email__iexact=serializer.validated_data['email'].strip()).first()
            if not user or not user.check_password(serializer.validated_data['password']):
                return api_error('Identifiants invalides.', status_code=401)
            if not user.is_active or not user.is_email_verified:
                return api_error('Compte inactif ou e-mail non vérifié.', status_code=403)
            pair = issue_pair(user, ip_address=request.META.get('REMOTE_ADDR') or None,
                user_agent=request.headers.get('User-Agent', ''))
            User.objects.filter(pk=user.pk).update(last_login_at=timezone.now())
            return api_success({'tokens': pair, 'user': UserSerializer(user, context={'request': request}).data},
                'Connexion réussie.')
        except Exception as exc:
            return handle_error(exc, 'login')

class RefreshAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh'

    @refresh_swagger
    def post(self, request):
        try:
            serializer = RefreshSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            pair = rotate_refresh(serializer.validated_data['refresh'],
                ip_address=request.META.get('REMOTE_ADDR') or None,
                user_agent=request.headers.get('User-Agent', ''))
            return api_success({'tokens': pair}, 'Session renouvelée.')
        except Exception as exc:
            return handle_error(exc, 'refresh')

class LogoutAPIView(APIView):
    permission_classes = [IsClientUser]
    @logout_swagger
    def post(self, request):
        try:
            serializer = LogoutSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user, claims = decode_token(serializer.validated_data['refresh'], expected_kind='refresh')
            if user.pk != request.user.pk:
                return api_error('Jeton ne correspondant pas au compte.', status_code=403)
            session = validate_active_session(user, claims, check_refresh_digest=True)
            session.revoked_at = timezone.now()
            session.save(update_fields=['revoked_at', 'updated_at'])
            return api_success(message='Session déconnectée.')
        except Exception as exc:
            return handle_error(exc, 'logout')

class LogoutAllAPIView(APIView):
    permission_classes = [IsClientUser]
    @logout_all_swagger
    def post(self, request):
        try:
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                user.token_version += 1
                user.save(update_fields=['token_version', 'updated_at'])
                revoke_all_user_sessions(user)
            return api_success(message='Toutes les sessions ont été révoquées.')
        except Exception as exc:
            return handle_error(exc, 'logout_all')

class ForgotPasswordAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'reset'

    @forgot_password_swagger
    def post(self, request):
        try:
            serializer = EmailSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = User.objects.filter(email__iexact=serializer.validated_data['email'].strip(), is_active=True).first()
            if user and user.is_email_verified:
                send_reset(user)
            return api_success(message='Si cette adresse est enregistrée, un lien a été envoyé.')
        except Exception as exc:
            return handle_error(exc, 'forgot_password')

class ResetPasswordAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'reset'

    @reset_password_swagger
    def post(self, request):
        try:
            serializer = ResetPasswordSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            with transaction.atomic():
                record = UserActionToken.objects.select_for_update().select_related('user').filter(
                    purpose=UserActionToken.Purpose.RESET_PASSWORD, token_hash=token_digest(serializer.validated_data['token'])).first()
                if not record or record.consumed_at or record.expires_at <= timezone.now():
                    return api_error('Lien de réinitialisation invalide ou expiré.', status_code=400)
                user = User.objects.select_for_update().get(pk=record.user_id)
                if not user.is_active:
                    return api_error('Compte désactivé.', status_code=403)
                user.set_password(serializer.validated_data['new_password'])
                user.token_version += 1
                user.password_changed_at = timezone.now()
                user.save(update_fields=['password', 'token_version', 'password_changed_at', 'updated_at'])
                revoke_all_user_sessions(user)
                record.consumed_at = timezone.now()
                record.save(update_fields=['consumed_at', 'updated_at'])
            return api_success(message='Mot de passe modifié. Reconnectez-vous.')
        except Exception as exc:
            return handle_error(exc, 'reset_password')

class ChangePasswordAPIView(APIView):
    permission_classes = [IsClientUser]
    @change_password_swagger
    def post(self, request):
        try:
            serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
            serializer.is_valid(raise_exception=True)
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                if not user.check_password(serializer.validated_data['current_password']):
                    return api_error('Mot de passe actuel incorrect.', status_code=400)
                user.set_password(serializer.validated_data['new_password'])
                user.token_version += 1
                user.password_changed_at = timezone.now()
                user.save(update_fields=['password', 'token_version', 'password_changed_at', 'updated_at'])
                revoke_all_user_sessions(user)
            return api_success(message='Mot de passe modifié. Toutes les sessions ont été déconnectées.')
        except Exception as exc:
            return handle_error(exc, 'change_password')

class MeRetrieveAPIView(APIView):
    permission_classes = [IsClientUser]
    @me_get_swagger
    def get(self, request):
        try:
            return api_success({'user': UserSerializer(request.user, context={'request': request}).data}, 'Profil client.')
        except Exception as exc:
            return handle_error(exc, 'me_get')

class MeUpdateAPIView(APIView):
    permission_classes = [IsClientUser]
    parser_classes = [JSONParser]

    @me_patch_swagger
    def patch(self, request):
        try:
            serializer = MePatchSerializer(data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            for key, value in serializer.validated_data.items():
                setattr(request.user, key, value)
            if serializer.validated_data:
                request.user.save(update_fields=[*serializer.validated_data.keys(), 'updated_at'])
            return api_success({'user': UserSerializer(request.user, context={'request': request}).data}, 'Profil modifié.')
        except Exception as exc:
            return handle_error(exc, 'me_patch')

class MeAvatarUploadAPIView(APIView):
    permission_classes = [IsClientUser]
    """Endpoint multipart dédié, documenté en paramètres de formulaire uniquement."""
    parser_classes = [MultiPartParser, FormParser]

    @me_avatar_swagger
    def patch(self, request):
        try:
            serializer = MeAvatarSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            request.user.avatar = serializer.validated_data['avatar']
            request.user.save(update_fields=['avatar', 'updated_at'])
            # La suppression de l'ancienne image nécessite une politique de stockage dédiée.
            return api_success({'user': UserSerializer(request.user, context={'request': request}).data}, 'Avatar modifié.')
        except Exception as exc:
            return handle_error(exc, 'me_avatar')


class SessionsListAPIView(APIView):
    permission_classes = [IsClientUser]
    @sessions_list_swagger
    def get(self, request):
        try:
            sessions = UserSession.objects.filter(user=request.user).order_by('-created_at')[:100]
            return api_success({'sessions': SessionSerializer(sessions, many=True).data})
        except Exception as exc:
            return handle_error(exc, 'sessions_list')

class SessionsRevokeAPIView(APIView):
    permission_classes = [IsClientUser]
    @sessions_revoke_swagger
    def delete(self, request, session_id):
        try:
            session = UserSession.objects.filter(user=request.user, pk=session_id).first()
            if not session:
                return api_error('Session introuvable.', status_code=404)
            if session.revoked_at is None:
                session.revoked_at = timezone.now()
                session.save(update_fields=['revoked_at', 'updated_at'])
            return api_success(message='Session révoquée.')
        except Exception as exc:
            return handle_error(exc, 'sessions_revoke')

class MyPermissionsAPIView(APIView):
    permission_classes = [IsClientUser]
    @permissions_get_swagger
    def get(self, request):
        try:
            memberships = OrganizationMembership.objects.filter(user=request.user, is_active=True).select_related('organization')
            projects = ProjectMember.objects.filter(user=request.user).select_related('project')
            result = {'organizations': [{'id': str(m.organization_id), 'name': m.organization.name,
                'slug': m.organization.slug, 'role': m.role} for m in memberships],
                'projects': [{'id': str(m.project_id), 'name': m.project.name, 'role': m.role} for m in projects]}
            return api_success(result, 'Rôles et accès du client.')
        except Exception as exc:
            return handle_error(exc, 'permissions_get')
