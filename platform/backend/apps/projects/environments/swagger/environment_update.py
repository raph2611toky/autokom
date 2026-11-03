from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.environments.serializers import EnvironmentUpdateSerializer

schema = swagger_auto_schema(
    operation_summary='Modifier un environnement',
    tags=['Environments'],
    responses=responses('environment_update'), request_body=EnvironmentUpdateSerializer,
    security=[{'ClientBearer': []}],
)
