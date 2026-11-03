from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map
from apps.accounts.user.serializers import GitHubExchangeSerializer

schema = swagger_auto_schema(
    operation_summary='Échanger le ticket GitHub pour une session AUTOKOM',
    operation_description='Ticket à usage unique, valable 90 secondes. JWT client standard en réponse JSON. Aucun token GitHub retourné.',
    tags=['Comptes clients', 'GitHub Auth'],
    request_body=GitHubExchangeSerializer,
    responses=response_map('github_exchange', public=True), security=[],
)
