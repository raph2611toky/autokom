from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Lister les environnements',
    tags=['Environments'],
    responses=responses('environment_list'),
    security=[{'ClientBearer': []}],
)
