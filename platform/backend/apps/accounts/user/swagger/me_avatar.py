from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.accounts.user.swagger.schemas import response_map


schema = swagger_auto_schema(
    operation_summary='Envoyer une photo de profil (multipart)',
    operation_description='Authentification par JWT client. Aucun request_body : documentation uniquement par manual_parameters.',
    tags=['Comptes clients'],
    manual_parameters=[openapi.Parameter(
        name='avatar', in_=openapi.IN_FORM, type=openapi.TYPE_FILE,
        required=True, description='Fichier image transmis avec multipart/form-data.',
    )],
    responses=response_map('me_avatar', success_status=200, public=False, include_not_found=False),
    security=[{'ClientBearer': []}],
)
