from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import EmailSerializer


schema = swagger_auto_schema(
    operation_summary='Demander une réinitialisation',
    operation_description='Réponse identique qu’un compte existe ou non. Envoie un lien avec JWT signé puis AES-GCM, durée 30 min, utilisable une seule fois.',
    tags=['Comptes clients'],
    request_body=EmailSerializer,
    responses=response_map('forgot_password', success_status=200, public=True, include_not_found=False),
    security=[],
)
