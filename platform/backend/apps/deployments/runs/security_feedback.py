"""Read-only scanner summaries and explicitly requested GitHub/SMTP delivery."""
import re
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.deployments.runs.models import DeploymentRun, SecurityFeedbackAction
from apps.projects.git.services import _request, get_repository, get_user_token

RULE = re.compile(r'^[A-Za-z0-9_.-]{1,180}$')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
AI_STATES = {'DISABLED', 'UNAVAILABLE', 'NO_CANDIDATE', 'COMPLETE'}


def normalize_report(value):
    try:
        count, rules, ai = value['count'], value['rules'], value['ai']
        status, analyzed, classes = ai['status'], ai['analyzed'], ai.get('classes', {})
        scope = ai.get('scope', 'SEMGREP')
        if (scope not in ('SEMGREP', 'SOURCE') or type(count) is not int or not 0 <= count <= 50000 or not isinstance(rules, dict)
                or len(rules) > 20 or not isinstance(status, str) or status not in AI_STATES
                or type(analyzed) is not int or not 0 <= analyzed <= (24 if scope == 'SOURCE' else min(24, count))
                or not isinstance(classes, dict) or len(classes) > 24):
            raise ValueError
        if any(not RULE.fullmatch(k) or type(n) is not int or not 1 <= n <= count for k, n in rules.items()):
            raise ValueError
        if any(not re.fullmatch(r'[0-9]{1,3}', k) or type(n) is not int or not 1 <= n <= 24
               for k, n in classes.items()):
            raise ValueError
        if sum(rules.values()) > count or sum(classes.values()) != analyzed:
            raise ValueError
        if (status == 'COMPLETE') != (analyzed > 0):
            raise ValueError
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ValidationError('Format du résumé SAST invalide.') from None
    normalized = {'status': status, 'analyzed': analyzed, 'classes': classes}
    if scope == 'SOURCE':
        normalized['scope'] = scope
    return {'count': count, 'rules': rules, 'ai': normalized}


def message(run, report):
    # Never emit code, rule messages, paths, email addresses or private log URLs.
    sha = run.commit_sha if re.fullmatch(r'[a-f0-9]{40}', run.commit_sha) else 'non communiqué'
    return (f'Rapport SAST AUTOKOM pour le commit {sha}.\n\n'
            f'Semgrep a signalé {report["count"]} résultat(s) à examiner. '
            'Les extraits de code et le journal restent dans AUTOKOM.\n\n'
            f'DeepSAST : {report["ai"]["analyzed"]} emplacement(s) analysé(s) à titre consultatif. '
            'Le modèle ne modifie pas la décision Semgrep.')


class DeliveryConflict(APIException):
    status_code = 409
    default_detail = 'Action déjà demandée ou résultat incertain. Vérifier la destination avant toute relance.'


def deliver(run, actor, channel, target=0):
    if not settings.AUTOKOM_SECURITY_FEEDBACK_ENABLED:
        raise PermissionDenied('Envoi de rapports non activé sur cette instance.')
    if run.status not in ('FAILED', 'SUCCEEDED', 'BLOCKED') or not run.sast_report:
        raise ValidationError('Aucun résumé SAST disponible pour cette exécution terminée.')
    report = normalize_report(run.sast_report)
    if not report['count']:
        raise ValidationError('Aucun résultat Semgrep à notifier.')
    if channel not in ('ISSUE', 'PR', 'EMAIL') or type(target) is not int or not 0 <= target <= 2147483647:
        raise ValidationError('Canal ou destination invalide.')
    if (channel == 'PR') != bool(target):
        raise ValidationError('Seul un commentaire de pull request requiert un numéro.')
    if channel == 'EMAIL' and (not actor.is_email_verified or not actor.email or
            actor.email.casefold() not in settings.AUTOKOM_SECURITY_EMAIL_RECIPIENTS):
        raise PermissionDenied('Le courriel vérifié de ce compte doit être autorisé pour cet envoi.')
    if channel in ('ISSUE', 'PR'):
        project = run.environment.project
        connection = project.git_connection
        if not (connection and connection.is_active and connection.organization_id == project.organization_id
                and connection.provider == 'GITHUB' and connection.installation_id
                and REPO.fullmatch(project.repository_name or '')):
            raise PermissionDenied('Dépôt GitHub non autorisé.')
        _, repo = get_repository(actor, connection.installation_id, project.repository_external_id)
        if (str(repo.get('id')) != str(project.repository_external_id)
                or str(repo.get('full_name', '')).casefold() != project.repository_name.casefold()):
            raise PermissionDenied('Dépôt GitHub différent de la source autorisée.')
        token = get_user_token(actor)
        endpoint = '/repos/' + '/'.join(quote(p, safe='') for p in project.repository_name.split('/'))
        if channel == 'PR':
            pr = _request('GET', f'{endpoint}/pulls/{target}', token=token)
            if ((pr.get('head') or {}).get('sha') != run.commit_sha or
                    str(((pr.get('head') or {}).get('repo') or {}).get('id')) != str(project.repository_external_id)):
                raise ValidationError('La pull request doit pointer vers ce commit dans ce dépôt.')
    with transaction.atomic():
        DeploymentRun.objects.select_for_update().only('pk').get(pk=run.pk)
        action, created = SecurityFeedbackAction.objects.get_or_create(run=run, channel=channel, target=target)
        if not created:
            if action.completed_at:
                return {'already_sent': True, 'number': action.result_number or None}
            raise DeliveryConflict()
    body = message(run, report)
    try:
        number = 0
        if channel == 'EMAIL':
            if send_mail('AUTOKOM · Résumé SAST', body, settings.DEFAULT_FROM_EMAIL, [actor.email]) != 1:
                raise DeliveryConflict()
        else:
            resource = '/issues' if channel == 'ISSUE' else f'/issues/{target}/comments'
            data = {'body': body}
            if channel == 'ISSUE':
                data['title'] = 'AUTOKOM · Résultats SAST à examiner'
            reply = _request('POST', endpoint + resource, token=token, payload=data)
            number = reply.get('number' if channel == 'ISSUE' else 'id')
            if type(number) is not int or number < 1:
                raise DeliveryConflict()
        SecurityFeedbackAction.objects.filter(pk=action.pk).update(completed_at=timezone.now(), result_number=number)
    except Exception:
        # Do not repeat an external POST/SMTP send with an ambiguous outcome.
        raise DeliveryConflict() from None
    return {'already_sent': False, 'number': number or None}
