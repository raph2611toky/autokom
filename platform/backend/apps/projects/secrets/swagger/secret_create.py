from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.secrets.serializers import SecretWriteSerializer

schema = swagger_auto_schema(
    operation_summary='Enregistrer un secret chiffré',
    tags=['Secrets'],
    responses=responses('secret_create', success=201), request_body=SecretWriteSerializer,
    security=[{'ClientBearer': []}],
)
