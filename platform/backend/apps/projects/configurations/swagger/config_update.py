from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.configurations.serializers import DeploymentConfigWriteSerializer

schema = swagger_auto_schema(
    operation_summary='Modifier un brouillon',
    tags=['Configurations'],
    responses=responses('config_update'), request_body=DeploymentConfigWriteSerializer,
    security=[{'ClientBearer': []}],
)
