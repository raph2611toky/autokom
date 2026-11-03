"""Signed client JWTs; isolated from Django's administrator authentication."""
import uuid
import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from helpers.security import token_digest
from apps.accounts.user.models import User, UserSession

ALGORITHM = 'HS256'

def _encode(user, kind, *, session_id, jti, ttl):
    now = timezone.now()
    payload = {
        'iss': settings.CLIENT_JWT_ISSUER,
        'aud': settings.CLIENT_JWT_AUDIENCE,
        'sub': str(user.pk), 'sid': str(session_id),
        'jti': jti, 'ver': user.token_version, 'kind': kind,
        'iat': now, 'nbf': now, 'exp': now + ttl,
    }
    return jwt.encode(payload, settings.CLIENT_JWT_SIGNING_KEY, algorithm=ALGORITHM)

def decode_token(token, *, expected_kind):
    try:
        claims = jwt.decode(
            token, settings.CLIENT_JWT_SIGNING_KEY,
            algorithms=[ALGORITHM], audience=settings.CLIENT_JWT_AUDIENCE,
            issuer=settings.CLIENT_JWT_ISSUER,
            options={'require': ['sub', 'sid', 'jti', 'ver', 'kind', 'iss', 'aud', 'iat', 'nbf', 'exp']},
        )
        if claims.get('kind') != expected_kind:
            raise AuthenticationFailed('Type de jeton invalide.')
        user = User.objects.get(pk=claims['sub'])
        if not user.is_active or not user.is_email_verified:
            raise AuthenticationFailed('Compte non autorisé.')
        if int(claims['ver']) != user.token_version:
            raise AuthenticationFailed('Jeton révoqué.')
        return user, claims
    except (jwt.PyJWTError, User.DoesNotExist, ValueError, TypeError, KeyError) as exc:
        raise AuthenticationFailed('Jeton invalide ou expiré.') from exc

def issue_pair(user, *, ip_address=None, user_agent='', family=None):
    jti = str(uuid.uuid4())
    session = UserSession.objects.create(
        user=user, jti_hash=token_digest(jti),
        token_family=family or uuid.uuid4(), expires_at=timezone.now() + settings.CLIENT_REFRESH_LIFETIME,
        ip_address=ip_address, user_agent=user_agent[:255], last_seen_at=timezone.now(),
    )
    access = _encode(user, 'access', session_id=session.pk, jti=str(uuid.uuid4()), ttl=settings.CLIENT_ACCESS_LIFETIME)
    refresh = _encode(user, 'refresh', session_id=session.pk, jti=jti, ttl=settings.CLIENT_REFRESH_LIFETIME)
    return {'access': access, 'refresh': refresh, 'token_type': 'Bearer',
        'expires_in': int(settings.CLIENT_ACCESS_LIFETIME.total_seconds()), 'session_id': str(session.pk)}

def validate_active_session(user, claims, *, check_refresh_digest=False):
    try:
        session = UserSession.objects.get(pk=claims['sid'], user=user)
    except (UserSession.DoesNotExist, ValueError, TypeError):
        raise AuthenticationFailed('Session introuvable.')
    if not session.is_valid:
        raise AuthenticationFailed('Session expirée ou révoquée.')
    if check_refresh_digest and session.jti_hash != token_digest(claims['jti']):
        raise AuthenticationFailed('Jeton de rafraîchissement invalide.')
    return session

def rotate_refresh(raw_refresh, *, ip_address=None, user_agent=''):
    user, claims = decode_token(raw_refresh, expected_kind='refresh')
    replayed = False
    pair = None
    with transaction.atomic():
        try:
            session = UserSession.objects.select_for_update().get(pk=claims['sid'], user=user)
        except (UserSession.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed('Session introuvable.')
        now = timezone.now()
        if session.jti_hash != token_digest(claims['jti']):
            raise AuthenticationFailed('Jeton invalide.')
        if not session.is_valid:
            # Commit the family revocation BEFORE rejecting a replayed refresh.
            UserSession.objects.filter(user=user, token_family=session.token_family,
                revoked_at__isnull=True).update(revoked_at=now)
            replayed = True
        else:
            session.revoked_at = now
            session.save(update_fields=['revoked_at', 'updated_at'])
            pair = issue_pair(user, ip_address=ip_address, user_agent=user_agent, family=session.token_family)
            session.replaced_by_id = pair['session_id']
            session.save(update_fields=['replaced_by', 'updated_at'])
    if replayed:
        raise AuthenticationFailed('Session expirée ou déjà utilisée.')
    return pair

def revoke_all_user_sessions(user):
    UserSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
