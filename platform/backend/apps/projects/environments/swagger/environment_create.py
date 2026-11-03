from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.environments.serializers import EnvironmentCreateSerializer

schema = swagger_auto_schema(
    operation_summary='Créer un environnement',
    tags=['Environments'],
    responses=responses('environment_create', success=201), request_body=EnvironmentCreateSerializer,
    security=[{'ClientBearer': []}],
)
