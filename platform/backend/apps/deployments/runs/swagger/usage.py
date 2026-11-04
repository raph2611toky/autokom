from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, O

schema = swagger_auto_schema(
    operation_summary='Ressources allouées et utilisation réelle Prometheus',
    tags=['Déploiements'],
    responses=envelope({'allocated':O,'observed':O}),
    security=[{'ClientBearer': []}],
)
