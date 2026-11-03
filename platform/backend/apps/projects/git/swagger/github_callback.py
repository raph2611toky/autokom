from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Retour OAuth sécurisé GitHub',
    tags=['GitHub'],
    responses={302: openapi.Response('Redirection vers le frontend, aucun JSON ni jeton renvoyé.')},
    manual_parameters=[openapi.Parameter('state', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True), openapi.Parameter('code', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False), openapi.Parameter('error', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False)],
    security=[],
)
