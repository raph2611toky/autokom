"""Client APIs. One HTTP method per APIView, guarded by object-level access."""
from django.utils import timezone
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.throttling import ScopedRateThrottle
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment, int_param
from apps.deployments.runs.models import DeploymentRun, DeploymentLogEntry
from apps.deployments.runs.serializers import (DeploymentRunSerializer, DeploymentLogSerializer,
                                                HighAvailabilityRequestSerializer)
from apps.deployments.runs.services import queue_manual, plan_deployment, request_ha
from apps.deployments.runs.runner_manifest import check_runner_signature, fetch_runner_manifest
from apps.deployments.runs.runtime_logs import fetch_runtime_logs
from apps.deployments.runs.metrics import fetch_usage
from apps.plans.entitlements.models import HighAvailabilityOption
from helpers.responses import api_success
from helpers.errors import handle_error
from apps.deployments.runs.swagger.preview import schema as preview_schema
from apps.deployments.runs.swagger.enqueue import schema as enqueue_schema
from apps.deployments.runs.swagger.list_runs import schema as list_runs_schema
from apps.deployments.runs.swagger.run_detail import schema as run_detail_schema
from apps.deployments.runs.swagger.run_logs import schema as run_logs_schema
from apps.deployments.runs.swagger.run_cancel import schema as run_cancel_schema
from apps.deployments.runs.swagger.ha_status import schema as ha_status_schema
from apps.deployments.runs.swagger.ha_request import schema as ha_request_schema
from apps.deployments.runs.swagger.runner_manifest import schema as runner_manifest_schema
from apps.deployments.runs.swagger.runtime_logs import schema as runtime_logs_schema
from apps.deployments.runs.swagger.usage import schema as usage_schema


def accessible_run(user, project_id, environment_id, run_id, *, write=False):
    _, env = get_environment(user, project_id, environment_id, write=write)
    run = DeploymentRun.objects.filter(environment=env, pk=run_id).first()
    if not run:
        raise NotFound('Exécution introuvable.')
    return run


class DeploymentPreviewAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    @preview_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            cfg, resources = plan_deployment(env)
            return api_success({'configuration_id': str(cfg.id), 'version': cfg.version,
                'repository': env.project.repository_name, 'branch': cfg.branch,
                'build_method': cfg.build_method, 'runtime_type': cfg.runtime_type,
                'root_directory': cfg.root_directory, 'mode': cfg.mode,
                'auto_deploy': cfg.auto_deploy, 'resources': resources},
                'Prévisualisation validée ; aucun build lancé.')
        except Exception as exc:
            return handle_error(exc, 'deployment_preview')


class DeploymentEnqueueAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'deploy'

    @enqueue_schema
    def post(self, request, project_id, environment_id):
        try:
            key = request.headers.get('Idempotency-Key')
            run, created = queue_manual(request.user, project_id, environment_id, idempotency_key=key)
            return api_success({'deployment': DeploymentRunSerializer(run).data,
                                 'enqueued': created}, 'Déploiement en file Jenkins.', 202)
        except Exception as exc:
            return handle_error(exc, 'deployment_enqueue')


class DeploymentListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    @list_runs_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            page = int_param(request, 'page', default=1, maximum=100000)
            size = int_param(request, 'page_size', default=20, maximum=50)
            qs = DeploymentRun.objects.filter(environment=env).order_by('-created_at')
            return api_success({'count': qs.count(), 'page': page,
                'runs': DeploymentRunSerializer(qs[(page-1)*size:page*size], many=True).data},
                'Historique des déploiements.')
        except Exception as exc:
            return handle_error(exc, 'deployment_list')


class DeploymentDetailAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    @run_detail_schema
    def get(self, request, project_id, environment_id, run_id):
        try:
            run = accessible_run(request.user, project_id, environment_id, run_id)
            return api_success({'deployment': DeploymentRunSerializer(run).data}, 'État de déploiement.')
        except Exception as exc:
            return handle_error(exc, 'deployment_detail')


class DeploymentLogsAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'deploy_logs'

    @run_logs_schema
    def get(self, request, project_id, environment_id, run_id):
        try:
            run = accessible_run(request.user, project_id, environment_id, run_id)
            after = int_param(request, 'after', default=0, minimum=0, maximum=100000000)
            qs = DeploymentLogEntry.objects.filter(deployment=run, sequence__gt=after).order_by('sequence')
            entries = list(qs[:100])
            return api_success({'logs': DeploymentLogSerializer(entries, many=True).data,
                'next_after': entries[-1].sequence if entries else after,
                'running': run.status in ('QUEUED','RUNNING')}, 'Journal filtré du déploiement.')
        except Exception as exc:
            return handle_error(exc, 'deployment_logs')


class DeploymentCancelAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    @run_cancel_schema
    def post(self, request, project_id, environment_id, run_id):
        try:
            with transaction.atomic():
                run = accessible_run(request.user, project_id, environment_id, run_id, write=True)
                run = DeploymentRun.objects.select_for_update().get(pk=run.pk)
                if run.status in ('QUEUED', 'RUNNING'):
                    run.cancel_requested = True
                    run.save(update_fields=['cancel_requested'])
                return api_success({'deployment_id': str(run.id), 'cancel_requested': run.cancel_requested},
                                   'Demande d’annulation enregistrée.')
        except Exception as exc:
            return handle_error(exc, 'deployment_cancel')


class HighAvailabilityStatusAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    @ha_status_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            option = HighAvailabilityOption.objects.filter(environment=env).first()
            return api_success({'ha': {
                'status': option.status if option else 'NOT_REQUESTED',
                'requested_replicas': option.desired_replicas if option else 1,
                'activated_at': option.activated_at if option else None,
                'expires_at': option.expires_at if option else None,
                'paid_feature': True, 'payment_integration_ready': False,
            }}, 'État de l’option haute disponibilité.')
        except Exception as exc:
            return handle_error(exc, 'ha_status')


class HighAvailabilityRequestAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    @ha_request_schema
    def post(self, request, project_id, environment_id):
        try:
            project, env = get_environment(request.user, project_id, environment_id, write=True)
            from apps.organizations.models import OrganizationMembership
            from rest_framework.exceptions import PermissionDenied
            if project.organization.owner_id != request.user.id and not OrganizationMembership.objects.filter(
                    organization=project.organization, user=request.user, is_active=True,
                    role__in=('OWNER','ADMIN')).exists():
                raise PermissionDenied('Seuls les administrateurs de l’organisation peuvent demander une option payante.')
            ser = HighAvailabilityRequestSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            option = request_ha(env, ser.validated_data['replicas'])
            return api_success({'ha': {'status': option.status, 'requested_replicas': option.desired_replicas,
                                      'payment_integration_ready': False}},
                               'Demande premium enregistrée, en attente de validation et de capacité multi-nœuds.', 202)
        except Exception as exc:
            return handle_error(exc, 'ha_request')


class RunnerManifestAPIView(APIView):
    """Internal read-only runner-only API; excludes API documentation and clients."""
    parser_classes = [JSONParser]
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'runner_manifest'

    @runner_manifest_schema
    def get(self, request, run_id):
        try:
            check_runner_signature(run_id, request.headers.get('X-Autokom-Runner-Time'),
                                   request.headers.get('X-Autokom-Runner-Signature'))
            from django.http import JsonResponse
            response = JsonResponse(fetch_runner_manifest(run_id))
            response['Cache-Control'] = 'no-store, private'
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        except Exception as exc:
            # Never expose cryptographic / secret details publicly.
            return handle_error(exc, 'runner_manifest')


class RuntimeLogsAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'deploy_logs'

    @runtime_logs_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            return api_success({'logs': fetch_runtime_logs(env)},
                               'Dernières lignes applicatives Loki (contenu filtré).')
        except Exception as exc:
            return handle_error(exc, 'runtime_logs')


class DeploymentUsageAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'deploy_logs'

    @usage_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            cfg, limits = plan_deployment(env)
            return api_success({'allocated':limits, 'observed':fetch_usage(env)},
                               'Ressources allouées et utilisation mesurée (si disponible).')
        except Exception as exc:
            return handle_error(exc, 'deployment_usage')
