from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Consulter la configuration',
    tags=['Configurations'],
    responses=responses('config_detail'),
    security=[{'ClientBearer': []}],
)
