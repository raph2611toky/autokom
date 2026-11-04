from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from apps.accounts.user.tokens import decode_token, validate_active_session

class ClientJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        authorization = request.headers.get('Authorization', '')
        if not authorization:
            return None
        scheme, sep, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not sep or not token.strip():
            raise AuthenticationFailed('Utiliser Authorization: Bearer <token>.')
        user, claims = decode_token(token.strip(), expected_kind='access')
        validate_active_session(user, claims)
        request.client_user = user
        request.client_claims = claims
        return (user, claims)

    def authenticate_header(self, request):
        return 'Bearer realm="autokom-clients"'


class ClientCookieJWTAuthentication(BaseAuthentication):
    """Authenticate SPA HttpOnly access cookie and ALWAYS enforce CSRF on writes.

    Bearer authentication has priority; public endpoints explicitly disable
    authentication and the separate browser login/refresh endpoints use
    csrf_protect even for unauthenticated requests.
    """
    def authenticate(self, request):
        # Never silently fall back from an invalid Authorization header to cookies.
        if request.headers.get('Authorization'):
            return None
        from apps.accounts.user.browser_views import ACCESS_COOKIE
        raw = request.COOKIES.get(ACCESS_COOKIE)
        if not raw:
            return None
        user, claims = decode_token(raw, expected_kind='access')
        validate_active_session(user, claims)
        from rest_framework.authentication import SessionAuthentication
        SessionAuthentication().enforce_csrf(request)
        request.client_user = user
        request.client_claims = claims
        return (user, claims)
