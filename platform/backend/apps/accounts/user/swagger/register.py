from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import RegisterSerializer


schema = swagger_auto_schema(
    operation_summary='Créer un compte client',
    operation_description='Accessible sans compte connecté.',
    tags=['Comptes clients'],
    request_body=RegisterSerializer,
    responses=response_map('register', success_status=201, public=True, include_not_found=False),
    security=[],
)
