from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Lister les dépôts autorisés',
    tags=['GitHub'],
    responses=responses('github_repositories'), manual_parameters=[openapi.Parameter('installation_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True), openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False)],
    security=[{'ClientBearer': []}],
)
