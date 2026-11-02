from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import LogoutSerializer


schema = swagger_auto_schema(
    operation_summary='Se déconnecter',
    operation_description='Authentification par JWT client.',
    tags=['Comptes clients'],
    request_body=LogoutSerializer,
    responses=response_map('logout', success_status=200, public=False, include_not_found=False),
    security=[{'ClientBearer': []}],
)
