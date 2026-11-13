"""Authenticated hand-off from Jenkins to the AUTOKOM runtime worker."""
from django.db import transaction
from rest_framework.exceptions import PermissionDenied
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun

REQUESTED = 'JENKINS_DAST_REQUESTED'
RUNNING = 'JENKINS_DAST_RUNNING'
PASSED = 'JENKINS_DAST_PASSED'
FAILED = 'JENKINS_DAST_FAILED'
DEFERRED = 'JENKINS_DAST_DEFERRED'


@transaction.atomic
def runner_dast_status(run_id):
    run = DeploymentRun.objects.select_for_update().filter(
        pk=run_id, status='RUNNING', mode_snapshot='DEVSECOPS',
        cancel_requested=False, ci_provider='JENKINS').first()
    if not run:
        raise PermissionDenied('Contrôle dynamique non autorisé pour cette publication.')
    stage = PipelineStageRun.objects.filter(deployment=run, stage_key='dast').first()
    if not stage:
        raise PermissionDenied('Étape dynamique absente.')
    if not stage.external_stage_id:
        stage.external_stage_id = REQUESTED
        stage.save(update_fields=['external_stage_id'])
    return stage.external_stage_id


def process_dast_previews():
    from apps.deployments.runs.autokom_socket import request_control
    from apps.deployments.runs.runner_manifest import fetch_runner_manifest
    with transaction.atomic():
        stage = (PipelineStageRun.objects.select_for_update(skip_locked=True)
                 .filter(stage_key='dast', external_stage_id=REQUESTED, deployment__status='RUNNING',
                         deployment__cancel_requested=False, deployment__mode_snapshot='DEVSECOPS',
                         deployment__ci_provider='JENKINS').order_by('deployment__created_at').first())
        if not stage:
            return False
        stage.external_stage_id = RUNNING
        stage.save(update_fields=['external_stage_id'])
    verdict = FAILED
    try:
        run = stage.deployment
        manifest = fetch_runner_manifest(run.pk)
        cfg = manifest['build']
        resource = manifest['resources']
        result = request_control('runtime.dynamic.preview', {
            'run_id': str(run.pk), 'internal_port': cfg['port'], 'healthcheck': cfg['healthcheck'],
            'memory_mb': resource['memory_mb'], 'cpu_millicores': resource['cpu_millicores'],
        }, timeout=260)
        http = result.get('http')
        if type(http) is not int or not 0 <= http <= 599:
            raise ValueError('Invalid HTTP result')
        if http == 0:
            # Isolated previews intentionally receive no live variables or DB.
            # The existing candidate check with the real config remains mandatory.
            verdict, summary = DEFERRED, 'Aucune réponse sans configuration live. Contrôle obligatoire sur le candidat AUTOKOM.'
        elif 200 <= http < 400:
            labels = {'CSP_MISSING':'CSP absente', 'NOSNIFF_MISSING':'nosniff absent',
                      'FRAME_POLICY_MISSING':'protection iframe absente'}
            warnings = ', '.join(v for k,v in labels.items() if k in (result.get('warnings') or []))
            verdict, summary = PASSED, f'Prévisualisation privée HTTP {http}. ' + (warnings or 'Aucun avertissement applicable.')
        else:
            verdict, summary = DEFERRED, f'Prévisualisation privée HTTP {http}. Contrôle obligatoire sur le candidat AUTOKOM.'
    except Exception as exc:
        import logging
        logging.getLogger('autokom.deployments').warning(
            'DAST privé indisponible run=%s (%s)', stage.deployment_id, type(exc).__name__)
        summary = 'Prévisualisation privée indisponible. Vérifier le broker AUTOKOM.'
    PipelineStageRun.objects.filter(pk=stage.pk, external_stage_id=RUNNING).update(
        external_stage_id=verdict, summary=summary)
    return True
