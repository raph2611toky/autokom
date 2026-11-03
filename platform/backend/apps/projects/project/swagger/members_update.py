from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.project.serializers import ProjectMemberUpdateSerializer

schema = swagger_auto_schema(
    operation_summary='Modifier le rôle d’un collaborateur',
    tags=['Project'],
    responses=responses('members_update', success=200),
    request_body=ProjectMemberUpdateSerializer,
    security=[{'ClientBearer': []}],
)
