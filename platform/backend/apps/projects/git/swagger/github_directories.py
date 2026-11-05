from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Parcourir les dossiers et détecter le framework depuis les métadonnées du dépôt autorisé',
    tags=['GitHub'], responses=responses('github_directories'),
    manual_parameters=[
        openapi.Parameter('installation_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
        openapi.Parameter('repository_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
        openapi.Parameter('branch', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True),
        openapi.Parameter('path', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
    ], security=[{'ClientBearer': []}],
)
