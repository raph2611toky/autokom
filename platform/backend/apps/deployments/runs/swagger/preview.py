from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Prévisualiser un déploiement autorisé',
    tags=['Déploiements'],
    responses=envelope({'configuration_id': S, 'version': I, 'repository': S, 'branch': S, 'build_method': S, 'runtime_type': S, 'root_directory': S, 'mode': S, 'auto_deploy': B, 'resources': O}, status=200),
    security=[{'ClientBearer': []}],
)
