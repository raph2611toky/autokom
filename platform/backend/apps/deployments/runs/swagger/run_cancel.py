from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Annuler un déploiement',
    tags=['Déploiements'],
    responses=envelope({'deployment_id': S, 'cancel_requested': B}, status=200),
    security=[{'ClientBearer': []}],
)
