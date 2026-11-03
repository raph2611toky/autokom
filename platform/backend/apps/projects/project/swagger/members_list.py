from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Lister les collaborateurs',
    tags=['Project'],
    responses=responses('members_list', success=200),
    security=[{'ClientBearer': []}],
)
