"""Authenticated and project-scoped two-service API, additive to legacy routes."""
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.exceptions import NotFound
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_project, int_param
from apps.projects.configurations.models import ServiceStack
from apps.projects.environments.models import Environment
from apps.deployments.runs.models import DeploymentRun, StackDeployment
from apps.deployments.runs.serializers import DeploymentRunSerializer
from apps.projects.configurations.stack_services import (
    StackConfigureSerializer, configure_stack, stack_summary, launch_stack, preflight_stack_domains,
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


class StackDomainPreflightAPIView(APIView):
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'domains'
    permission_classes = [IsClientUser]

    def get(self, request, project_id):
        try:
            from django.conf import settings
            from apps.deployments.runs.autokom_socket import request_control
            get_project(request.user, project_id)
            info = request_control('domain.config.check', {
                'managed_suffix': settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX,
            }, timeout=15)
            return api_success({'ingress_ipv4': info['ingress_ipv4'],
                                'managed_suffix': info['managed_suffix']},
                               'Adresse DNS attendue pour les domaines de votre projet.')
        except Exception as exc:
            return handle_error(exc, 'stack_domain_settings')

    def post(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            stack = ServiceStack.objects.filter(project=project).first()
            if not stack:
                raise NotFound('Configurer les deux services et leurs domaines avant validation DNS.')
            checks = preflight_stack_domains(stack)
            return api_success({'domains': checks}, 'DNS vérifié pour les deux services ; aucun conteneur publié.')
        except Exception as exc:
            return handle_error(exc, 'stack_domain_preflight')


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


def group_release_status(group, active_ids):
    if group.status != 'SUCCEEDED' or not group.frontend_run_id:
        return ''
    members = {group.backend_run_id, group.frontend_run_id}
    return 'ACTIVE' if members <= active_ids else 'SUPERSEDED' if members.isdisjoint(active_ids) else ''


def active_group_runs(project):
    from apps.deployments.sites.models import HostedApplication
    return set(HostedApplication.objects.filter(environment__project=project, observed_state='RUNNING',
        current_release__status='ACTIVE').values_list('current_release__source_deployment_id', flat=True))


class StackRestoreAPIView(APIView):
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'deploy'

    def post(self, request, project_id, stack_run_id):
        try:
            from apps.deployments.runs.restore import queue_stack_restore
            project = get_project(request.user, project_id, write=True)
            source = StackDeployment.objects.filter(stack__project=project, pk=stack_run_id).first()
            if source is None:
                raise NotFound('Publication groupée introuvable.')
            run, created = queue_stack_restore(source, request.user, request.headers.get('Idempotency-Key'))
            return api_success({'deployment':{'id':str(run.pk)}, 'enqueued':created},
                               'Restauration groupée mise en file.', 202)
        except Exception as exc:
            return handle_error(exc, 'stack_restore')


class StackDeploymentDetailAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    def get(self, request, project_id, stack_run_id):
        try:
            project = get_project(request.user, project_id)
            run = StackDeployment.objects.filter(pk=stack_run_id, stack__project=project).select_related(
                'backend_run', 'frontend_run').first()
            if run is None:
                raise NotFound('Publication groupée introuvable.')
            return api_success({'deployment': {
                'id': str(run.id), 'status': run.status, 'error_code': run.error_code,
                'repository_name': project.repository_name,
                'release_status': group_release_status(run, active_group_runs(project)),
                'trigger_type': run.backend_run.trigger_type,
                'created_at': run.created_at, 'finished_at': run.finished_at,
                'backend': {'run_id': str(run.backend_run_id), 'environment_id': str(run.backend_run.environment_id),
                            'status': run.backend_run.status},
                'frontend': {'run_id': str(run.frontend_run_id), 'environment_id': str(run.frontend_run.environment_id),
                             'status': run.frontend_run.status} if run.frontend_run else
                            {'run_id': None, 'environment_id': str(run.stack.frontend_environment_id), 'status': 'PENDING'},
            }}, 'Progression de la publication groupée.')
        except Exception as exc:
            return handle_error(exc, 'stack_run_detail')


class StackPublicationHistoryAPIView(APIView):
    """Project activity feed: one group entry for two child runs, plus legacy runs.

    This is read-only; child runs remain accessible only through scoped log APIs.
    Paginate after merging both streams to preserve true chronological order.
    """
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    def get(self, request, project_id):
        try:
            project = get_project(request.user, project_id)
            page = int_param(request, 'page', default=1, maximum=100000)
            size = int_param(request, 'page_size', default=20, maximum=50)
            primary = Environment.objects.filter(project=project, slug='production').first()
            legacy = (DeploymentRun.objects.filter(environment=primary)
                      .filter(backend_stack_execution__isnull=True, frontend_stack_execution__isnull=True)
                      .prefetch_related('stages','releases','security_feedback')
                      .order_by('-created_at', '-id')) if primary else DeploymentRun.objects.none()
            groups = (StackDeployment.objects.filter(stack__project=project)
                      .select_related('backend_run', 'frontend_run')
                      .order_by('-created_at', '-id'))
            count = legacy.count() + groups.count()
            upper = min(page * size, count)
            if (page - 1) * size >= count:
                return api_success({'count': count, 'page': page, 'runs': []},
                                   'Historique consolidé des publications.')
            # Each stream only needs its first `upper` records for a correct merged page.
            candidates = ([(run.created_at, str(run.id), 'single', run) for run in legacy[:upper]] +
                          [(run.created_at, str(run.id), 'stack', run) for run in groups[:upper]])
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            items = []
            active_ids = active_group_runs(project)
            for _, _, kind, run in candidates[(page - 1) * size:upper]:
                if kind == 'single':
                    item = dict(DeploymentRunSerializer(run).data)
                    item['kind'] = 'single'
                else:
                    item = {'id': str(run.id), 'kind': 'stack', 'status': run.status,
                            'created_at': run.created_at, 'finished_at': run.finished_at,
                            'trigger_type': run.backend_run.trigger_type,
                            'release_status': group_release_status(run, active_ids),
                            'backend_status': run.backend_run.status,
                            'frontend_status': run.frontend_run.status if run.frontend_run else 'PENDING',
                            'error_code': run.error_code,
                            'security_warning': any(bool(r and ((r.sca_report or {}).get('count')
                                or (r.sca_report or {}).get('secrets') or (r.sast_report or {}).get('count')
                                or (r.sast_report or {}).get('ai', {}).get('status') == 'UNAVAILABLE'))
                                for r in (run.backend_run,run.frontend_run))}
                items.append(item)
            return api_success({'count': count, 'page': page, 'runs': items},
                               'Historique consolidé des publications.')
        except Exception as exc:
            return handle_error(exc, 'stack_publication_history')
