from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.environments.serializers import EnvironmentVariableWriteSerializer

schema = swagger_auto_schema(
    operation_summary='Créer une variable',
    tags=['Environments'],
    responses=responses('variables_create', success=201), request_body=EnvironmentVariableWriteSerializer,
    security=[{'ClientBearer': []}],
)
