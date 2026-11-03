from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

schema = swagger_auto_schema(
    operation_summary='Callback GitHub App de connexion client',
    operation_description='Consomme le state et PKCE, vérifie l’identité GitHub et redirige vers le frontend avec un ticket opaque uniquement en fragment URL. Ce callback est distinct du callback des intégrations projets.',
    tags=['Comptes clients', 'GitHub Auth'],
    manual_parameters=[
        openapi.Parameter('code', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
        openapi.Parameter('state', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True),
        openapi.Parameter('error', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
    ],
    responses={302: openapi.Response('Redirection vers /auth/github/callback#status=...&ticket=...')},
    security=[],
)
