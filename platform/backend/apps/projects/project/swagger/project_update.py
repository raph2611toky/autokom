from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.project.serializers import ProjectPatchSerializer

schema = swagger_auto_schema(
    operation_summary='Modifier un projet',
    tags=['Project'],
    responses=responses('project_update'), request_body=ProjectPatchSerializer,
    security=[{'ClientBearer': []}],
)
