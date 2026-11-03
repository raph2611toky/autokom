from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses
from apps.projects.project.serializers import ProjectMemberCreateSerializer

schema = swagger_auto_schema(
    operation_summary='Ajouter un collaborateur',
    tags=['Project'],
    responses=responses('members_add', success=201),
    request_body=ProjectMemberCreateSerializer,
    security=[{'ClientBearer': []}],
)
