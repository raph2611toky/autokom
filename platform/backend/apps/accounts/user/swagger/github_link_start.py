from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map

schema = swagger_auto_schema(
    operation_summary='Associer GitHub à un compte client déjà connecté',
    operation_description='Nécessite un JWT client valide. Ne fusionne jamais des comptes sur l’e-mail seul.',
    tags=['Comptes clients', 'GitHub Auth'],
    responses=response_map('github_start'),
)
