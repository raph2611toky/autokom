from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Supprimer une variable',
    tags=['Environments'],
    responses=responses('variable_delete'),
    security=[{'ClientBearer': []}],
)
