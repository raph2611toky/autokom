"""Authenticated and project-scoped two-service API, additive to legacy routes."""
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.exceptions import NotFound
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_project
from apps.projects.configurations.models import ServiceStack
from apps.projects.configurations.stack_services import (
    StackConfigureSerializer, configure_stack, stack_summary, launch_stack,
)
from helpers.responses import api_success
from helpers.errors import handle_error


class StackAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    def get(self, request, project_id):
        try:
            project = get_project(request.user, project_id)
            stack = ServiceStack.objects.filter(project=project).first()
            return api_success({'stack': stack_summary(stack) if stack else None}, 'Mode frontend + backend.')
        except Exception as exc:
            return handle_error(exc, 'stack_detail')

    def put(self, request, project_id):
        try:
            get_project(request.user, project_id, write=True)
            serializer = StackConfigureSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            stack = configure_stack(request.user, project_id, serializer.validated_data)
            return api_success({'stack': stack_summary(stack)}, 'Deux services configurés. Aucun déploiement lancé.')
        except Exception as exc:
            return handle_error(exc, 'stack_configure')


class StackLaunchAPIView(APIView):
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'deploy'
    permission_classes = [IsClientUser]

    def post(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            stack = ServiceStack.objects.filter(project=project).first()
            if not stack:
                raise NotFound('Configurer le frontend et le backend avant de publier.')
            key = request.data.get('idempotency_key')
            run, created = launch_stack(request.user, stack, idempotency_key=key)
            return api_success({'stack_deployment': {'id': str(run.id), 'status': run.status,
                'backend_run_id': str(run.backend_run_id), 'frontend_run_id': str(run.frontend_run_id) if run.frontend_run_id else None},
                'created': created}, 'Publication groupée mise en file.', 202)
        except Exception as exc:
            return handle_error(exc, 'stack_launch')


class StackDeploymentDetailAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    def get(self, request, project_id, stack_run_id):
        try:
            from apps.deployments.runs.models import StackDeployment
            project = get_project(request.user, project_id)
            run = StackDeployment.objects.filter(pk=stack_run_id, stack__project=project).select_related(
                'backend_run', 'frontend_run').first()
            if run is None:
                raise NotFound('Publication groupée introuvable.')
            return api_success({'deployment': {
                'id': str(run.id), 'status': run.status, 'error_code': run.error_code,
                'backend': {'run_id': str(run.backend_run_id), 'status': run.backend_run.status},
                'frontend': {'run_id': str(run.frontend_run_id), 'status': run.frontend_run.status}
                  if run.frontend_run else {'run_id': None, 'status': 'PENDING'},
            }}, 'Progression de la publication groupée.')
        except Exception as exc:
            return handle_error(exc, 'stack_run_detail')
