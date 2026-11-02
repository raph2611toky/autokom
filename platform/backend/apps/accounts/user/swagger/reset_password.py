from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import ResetPasswordSerializer


schema = swagger_auto_schema(
    operation_summary='Réinitialiser le mot de passe',
    operation_description='Accessible sans compte connecté.',
    tags=['Comptes clients'],
    request_body=ResetPasswordSerializer,
    responses=response_map('reset_password', success_status=200, public=True, include_not_found=False),
    security=[],
)
