from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import MePatchSerializer


schema = swagger_auto_schema(
    operation_summary='Modifier son nom',
    operation_description='Authentification par JWT client.',
    tags=['Comptes clients'],
    request_body=MePatchSerializer,
    responses=response_map('me_patch', success_status=200, public=False, include_not_found=False),
    security=[{'ClientBearer': []}],
)
