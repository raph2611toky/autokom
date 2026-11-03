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
