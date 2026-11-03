from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.environments.serializers import EnvironmentVariableWriteSerializer

schema = swagger_auto_schema(
    operation_summary='Modifier une variable',
    tags=['Environments'],
    responses=responses('variable_update'), request_body=EnvironmentVariableWriteSerializer,
    security=[{'ClientBearer': []}],
)
