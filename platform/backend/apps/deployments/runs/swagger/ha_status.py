from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Statut option premium haute disponibilité',
    tags=['Déploiements','Haute disponibilité'],
    responses=envelope({'ha': O}, status=200),
    security=[{'ClientBearer': []}],
)
