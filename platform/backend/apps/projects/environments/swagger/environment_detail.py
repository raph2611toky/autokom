from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Consulter un environnement',
    tags=['Environments'],
    responses=responses('environment_detail'),
    security=[{'ClientBearer': []}],
)
