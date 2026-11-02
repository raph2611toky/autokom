from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map


schema = swagger_auto_schema(
    operation_summary='Consulter son profil',
    operation_description='Authentification par JWT client.',
    tags=['Comptes clients'],
    responses=response_map('me_get', success_status=200, public=False, include_not_found=False),
    security=[{'ClientBearer': []}],
)
