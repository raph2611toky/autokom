from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Lister les déploiements',
    tags=['Déploiements'],
    responses=envelope({'runs': openapi.Schema(type=openapi.TYPE_ARRAY, items=O), 'count': I, 'page': I}, status=200),
    security=[{'ClientBearer': []}],
)
