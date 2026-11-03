from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import ChangePasswordSerializer


schema = swagger_auto_schema(
    operation_summary='Modifier le mot de passe',
    operation_description='Authentification par JWT client.',
    tags=['Comptes clients'],
    request_body=ChangePasswordSerializer,
    responses=response_map('change_password', success_status=200, public=False, include_not_found=False),
    security=[{'ClientBearer': []}],
)
