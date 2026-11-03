from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map


schema = swagger_auto_schema(
    operation_summary='Révoquer toutes les sessions',
    operation_description='Authentification par JWT client.',
    tags=['Comptes clients'],
    responses=response_map('logout_all', success_status=200, public=False, include_not_found=False),
    security=[{'ClientBearer': []}],
)
