from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.environments.serializers import DomainCreateSerializer

schema = swagger_auto_schema(
    operation_summary='Réserver un domaine',
    tags=['Environments'],
    responses=responses('domains_create', success=201), request_body=DomainCreateSerializer,
    security=[{'ClientBearer': []}],
)
