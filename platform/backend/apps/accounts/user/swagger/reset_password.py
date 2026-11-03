from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import ResetPasswordSerializer


schema = swagger_auto_schema(
    operation_summary='Réinitialiser le mot de passe',
    operation_description='Accepte un jeton ar1 JWT signé puis AES-GCM, vérifie l’expiration et l’empreinte en base, consomme le jeton une fois et révoque les sessions. Ancien format accepté jusqu’à son expiration.',
    tags=['Comptes clients'],
    request_body=ResetPasswordSerializer,
    responses=response_map('reset_password', success_status=200, public=True, include_not_found=False),
    security=[],
)
