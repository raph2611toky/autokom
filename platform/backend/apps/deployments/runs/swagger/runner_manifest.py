from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.runs.swagger.schemas import envelope, S, I, B, O

schema = swagger_auto_schema(
    operation_summary='Manifest interne du runner signé par HMAC',
    tags=['Déploiements'],
    responses=envelope({'run_id': S, 'repository': S, 'commit_sha': S, 'build': O, 'resources': O, 'variables': O}, status=200),
    security=[], manual_parameters=[openapi.Parameter('X-Autokom-Runner-Time', openapi.IN_HEADER, type=openapi.TYPE_INTEGER, required=True),openapi.Parameter('X-Autokom-Runner-Signature', openapi.IN_HEADER, type=openapi.TYPE_STRING, required=True)],
)
