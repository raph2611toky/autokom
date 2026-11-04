from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O
from apps.deployments.runs.serializers import HighAvailabilityRequestSerializer

schema = swagger_auto_schema(
    operation_summary='Demander une option HA payante (sans paiement automatique)',
    tags=['Déploiements','Haute disponibilité'],
    responses=envelope({'ha': O}, status=202),
    security=[{'ClientBearer': []}],
    request_body=HighAvailabilityRequestSerializer,
)
