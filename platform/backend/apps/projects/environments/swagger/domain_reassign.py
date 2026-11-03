from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.environments.serializers import DomainReassignSerializer

schema = swagger_auto_schema(
    operation_summary='Réaffecter un domaine',
    tags=['Environments'],
    responses=responses('domain_reassign'), request_body=DomainReassignSerializer,
    security=[{'ClientBearer': []}],
)
