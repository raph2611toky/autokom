import hashlib
import hmac
import json
import re
import time

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.user.permissions import IsClientUser
from apps.deployments.runs.models import DeploymentRun
from apps.deployments.runs.security_feedback import deliver, normalize_report, normalize_sca
from apps.deployments.runs.views import accessible_run
from helpers.errors import handle_error
from helpers.responses import api_success


def verify_report_signature(run_id, epoch, signature, payload, kind='sast'):
    key = settings.AUTOKOM_RUNNER_SHARED_KEY
    if (len(key) < 32 or not re.fullmatch(r'[0-9]{1,12}', epoch or '')
            or abs(time.time() - int(epoch)) > 60
            or not re.fullmatch(r'[a-f0-9]{64}', signature or '')):
        raise PermissionDenied('Signature de rapport non valide.')
    digest = hashlib.sha256(payload).hexdigest()
    proof = hmac.new(key.encode(), f'autokom-{kind}:v1:{epoch}:{run_id}:{digest}'.encode(),
                     hashlib.sha256).hexdigest()
    if not hmac.compare_digest(proof, signature):
        raise PermissionDenied('Signature de rapport non valide.')


class RunnerSASTReportAPIView(APIView):
    swagger_schema = None
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'runner_manifest'

    kind = 'sast'

    def post(self, request, run_id):
        try:
            if not settings.AUTOKOM_SECURITY_REPORTS_ENABLED:
                raise PermissionDenied('Collecte des rapports de sécurité indisponible.')
            size = request.META.get('CONTENT_LENGTH') or ''
            if not re.fullmatch(r'[0-9]{1,5}', size) or not 1 <= int(size) <= 16384:
                raise PermissionDenied('Taille du résumé refusée.')
            payload = request.body
            if len(payload) != int(size):
                raise PermissionDenied('Taille du résumé refusée.')
            verify_report_signature(run_id, request.headers.get('X-Autokom-Runner-Time'),
                                    request.headers.get('X-Autokom-Runner-Signature'), payload, self.kind)
            try:
                report = (normalize_report if self.kind == 'sast' else normalize_sca)(json.loads(payload))
            except (ValueError, RecursionError):
                raise PermissionDenied('Résumé de sécurité illisible.') from None
            field = self.kind + '_report'
            with transaction.atomic():
                run = DeploymentRun.objects.select_for_update().filter(
                    pk=run_id, status='RUNNING', mode_snapshot='DEVSECOPS', cancel_requested=False).first()
                if run is None or (getattr(run, field) and getattr(run, field) != report):
                    raise PermissionDenied('Rapport non autorisé pour ce run.')
                if not getattr(run, field):
                    setattr(run, field, report)
                    run.save(update_fields=[field])
            return api_success({}, 'Résumé de sécurité enregistré.', 201)
        except Exception as exc:
            return handle_error(exc, 'runner_sast_report')


class RunnerSCAReportAPIView(RunnerSASTReportAPIView):
    kind = 'sca'


class RunnerDASTAPIView(RunnerSASTReportAPIView):
    def post(self, request, run_id):
        try:
            # Distinct signature so a read-only manifest signature is not reusable.
            if request.META.get('CONTENT_LENGTH') != '2' or request.body != b'{}':
                raise PermissionDenied('Requête de contrôle dynamique non valide.')
            verify_report_signature(run_id, request.headers.get('X-Autokom-Runner-Time'),
                                    request.headers.get('X-Autokom-Runner-Signature'), b'{}', 'dast')
            from apps.deployments.runs.runner_dast import runner_dast_status
            return api_success({'status':runner_dast_status(run_id)}, 'Contrôle dynamique du runner.')
        except Exception as exc:
            return handle_error(exc, 'runner_dast')


class SecurityFeedbackAPIView(APIView):
    permission_classes = [IsClientUser]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'security_feedback'

    def post(self, request, project_id, environment_id, run_id):
        try:
            run = accessible_run(request.user, project_id, environment_id, run_id, write=True)
            result = deliver(run, request.user, request.data.get('channel'), request.data.get('target', 0))
            return api_success(result, 'Rapport envoyé.')
        except Exception as exc:
            return handle_error(exc, 'security_feedback')
