from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import TokenSerializer


schema = swagger_auto_schema(
    operation_summary='Vérifier l’adresse e-mail',
    operation_description='Accessible sans compte connecté.',
    tags=['Comptes clients'],
    request_body=TokenSerializer,
    responses=response_map('email_verify', success_status=200, public=True, include_not_found=False),
    security=[],
)
