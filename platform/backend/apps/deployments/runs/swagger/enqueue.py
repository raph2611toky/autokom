from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Mettre un déploiement en file',
    tags=['Déploiements'],
    responses=envelope({'deployment': O, 'enqueued': B}, status=202),
    security=[{'ClientBearer': []}],
    manual_parameters=[openapi.Parameter('Idempotency-Key', openapi.IN_HEADER, description='Identifiant unique optionnel, 12–128 caractères', type=openapi.TYPE_STRING)],
)
