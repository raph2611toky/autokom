from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.secrets.serializers import SecretReplaceSerializer

schema = swagger_auto_schema(
    operation_summary='Remplacer un secret',
    tags=['Secrets'],
    responses=responses('secret_replace'), request_body=SecretReplaceSerializer,
    security=[{'ClientBearer': []}],
)
