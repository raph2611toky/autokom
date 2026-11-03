from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map

schema = swagger_auto_schema(
    operation_summary='Connexion ou inscription avec GitHub — démarrage',
    operation_description='Renvoie une URL officielle de GitHub App avec state à usage unique et PKCE S256.',
    tags=['Comptes clients', 'GitHub Auth'],
    manual_parameters=[openapi.Parameter('intent', openapi.IN_QUERY, type=openapi.TYPE_STRING,
                                        enum=['login', 'signup'], required=False, default='login')],
    responses=response_map('github_start', public=True), security=[],
)
