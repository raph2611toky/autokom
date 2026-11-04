"""Trusted runner-only manifest, authenticated with HMAC and current run state.

Never expose this endpoint to clients. Require TLS, isolated Jenkins agents and a
scoped Jenkins credentials store. Secret values are never returned to client APIs.
"""
import hashlib
import hmac
import re
import time
from django.conf import settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.deployments.runs.models import DeploymentRun
from apps.projects.environments.models import EnvironmentVariable
from apps.projects.git.services import decrypt


def check_runner_signature(run_id, epoch, signature):
    key = getattr(settings, 'AUTOKOM_RUNNER_SHARED_KEY', '')
    try:
        timestamp = int(epoch)
    except (ValueError, TypeError):
        raise PermissionDenied('Authentification du runner refusée.')
    if len(key) < 32 or abs(time.time() - timestamp) > 60 or not re.fullmatch(r'[a-f0-9]{64}', signature or ''):
        raise PermissionDenied('Authentification du runner refusée.')
    message = f'autokom-runner:v1:{timestamp}:{run_id}'.encode()
    expected = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise PermissionDenied('Authentification du runner refusée.')


def fetch_runner_manifest(run_id):
    run = (DeploymentRun.objects.select_related('environment__project','configuration','environment__project__git_connection')
           .filter(pk=run_id, status=DeploymentRun.Status.RUNNING).first())
    if run is None or run.cancel_requested:
        raise PermissionDenied('Déploiement non accessible au runner.')
    cfg = run.configuration
    env = run.environment
    project = env.project
    variables = {}
    for item in EnvironmentVariable.objects.filter(environment=env).select_related('secret'):
        if item.secret_id:
            sec = item.secret
            if not sec.is_active or sec.project_id != project.id or (sec.environment_id and sec.environment_id != env.id):
                raise ValidationError('Secret non autorisé pour cet environnement.')
            if sec.vault_reference:
                raise ValidationError('Coffre externe non relié au runner ; déploiement suspendu.')
            if not sec.encrypted_value:
                raise ValidationError('Secret vide.')
            variables[item.key] = decrypt(sec.encrypted_value)
        else:
            variables[item.key] = item.value
    domains = list(env.domains.filter(status='ACTIVE').values_list('hostname', flat=True)) if hasattr(env, 'domains') else []
    return {
        'run_id': str(run.id), 'environment_id': str(env.id), 'project_id': str(project.id),
        'repository': project.repository_name, 'repository_id': project.repository_external_id,
        'installation_id': project.git_connection.installation_id,
        'commit_sha': run.commit_sha, 'branch': run.branch,
        'build': {'method': cfg.build_method, 'runtime': cfg.runtime_type, 'root': cfg.root_directory,
                  'dockerfile': cfg.dockerfile_path, 'build_command': cfg.build_command,
                  'start_command': cfg.start_command, 'output_directory': cfg.output_directory,
                  'port': cfg.internal_port, 'healthcheck': cfg.healthcheck_path},
        'mode': run.mode_snapshot, 'resources': run.resources_snapshot,
        'variables': variables, 'domains': domains,
    }
