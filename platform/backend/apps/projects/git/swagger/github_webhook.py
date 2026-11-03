from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.projects.swagger_helpers import responses

schema = swagger_auto_schema(
    operation_summary='Recevoir un webhook signé',
    tags=['GitHub'],
    responses=responses('github_webhook', public=True), request_body=openapi.Schema(type=openapi.TYPE_OBJECT, description='Webhook GitHub JSON brut, signé HMAC-SHA256'), manual_parameters=[openapi.Parameter('X-Hub-Signature-256', openapi.IN_HEADER, type=openapi.TYPE_STRING, required=True), openapi.Parameter('X-GitHub-Delivery', openapi.IN_HEADER, type=openapi.TYPE_STRING, required=True), openapi.Parameter('X-GitHub-Event', openapi.IN_HEADER, type=openapi.TYPE_STRING, required=True)],
    security=[],
)
