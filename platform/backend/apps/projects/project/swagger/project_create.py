from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.project.serializers import ProjectCreateSerializer

schema = swagger_auto_schema(
    operation_summary='Créer un projet depuis GitHub',
    tags=['Project'],
    responses=responses('project_create', success=201), request_body=ProjectCreateSerializer,
    security=[{'ClientBearer': []}],
)
