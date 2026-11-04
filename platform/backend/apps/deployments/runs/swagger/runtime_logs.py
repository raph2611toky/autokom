from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Lire les journaux applicatifs Loki pour cet environnement',
    tags=['Déploiements'],
    responses=envelope({'logs':openapi.Schema(type=openapi.TYPE_ARRAY,items=O)}),
    security=[{'ClientBearer': []}],
)
