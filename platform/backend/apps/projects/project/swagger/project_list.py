from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Lister mes projets',
    tags=['Project'],
    responses=responses('project_list'), manual_parameters=[openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False), openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False), openapi.Parameter('organization_id', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False)],
    security=[{'ClientBearer': []}],
)
