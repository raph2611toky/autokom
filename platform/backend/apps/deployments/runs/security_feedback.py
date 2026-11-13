"""Read-only scanner summaries and explicitly requested GitHub/SMTP delivery."""
import re
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.projects.common import projects_for_user
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


def normalize_sca(value):
    try:
        counts = {name: value[name] for name in ('count','secrets','remaining')}
        remediated, rows = value['remediated'], value['findings']
        digest = value.get('lock_sha256', '')
        if (any(type(n) is not int or not 0 <= n <= 50000 for n in counts.values())
                or type(remediated) is not bool or not isinstance(rows, list) or len(rows) > min(32, counts['count'])
                or not isinstance(digest, str) or (remediated and not re.fullmatch(r'[a-f0-9]{64}', digest)) or (not remediated and digest)
                or (remediated and (not counts['count'] or counts['remaining'] or counts['secrets']))):
            raise ValueError
        patterns = {
            'id': r'[A-Z0-9a-z-]{4,60}',
            'package': r'(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]{1,100}',
            'installed': r'[0-9][0-9A-Za-z.+-]{0,60}',
        }
        safe = []
        for row in rows:
            clean = {key: row[key] for key in patterns}
            if any(not re.fullmatch(pattern, clean[key]) for key, pattern in patterns.items()):
                raise ValueError
            severity, fixed = row['severity'], row['fixed_versions']
            if severity not in ('HIGH', 'CRITICAL') or not isinstance(fixed, list) or len(fixed) > 6:
                raise ValueError
            if any(not re.fullmatch(patterns['installed'], version) for version in fixed):
                raise ValueError
            applied = row.get('applied_versions', [])
            if (not isinstance(applied, list) or len(applied) > 6 or (applied and not remediated)
                    or any(not re.fullmatch(patterns['installed'], version) for version in applied)):
                raise ValueError
            safe.append({**clean, 'severity':severity, 'fixed_versions':fixed, 'applied_versions':applied})
        return {**counts, 'remediated':remediated, 'findings':safe, 'lock_sha256':digest}
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ValidationError('Format du rapport Trivy invalide.') from None


SUGGESTIONS = {
    'autokom.javascript-eval': 'Remplacer eval par un parseur ou une liste d’opérations autorisées.',
    'autokom.javascript-function-constructor': 'Remplacer la construction de fonctions à partir de chaînes par des fonctions explicites.',
    'autokom.python-unsafe-eval': 'Utiliser un parseur de données adapté au format au lieu de eval.',
    'autokom.python-shell-true': 'Passer une liste d’arguments au sous-processus et éviter le shell.',
}


def reports(run):
    sast = normalize_report(run.sast_report) if run.sast_report else None
    sca = normalize_sca(run.sca_report) if getattr(run, 'sca_report', None) else None
    return sast, sca


def actionable(sast, sca):
    return bool((sast and (sast['count'] or sast['ai']['status'] == 'UNAVAILABLE'))
                or (sca and (sca['count'] or sca['secrets'])))


def message(run, report, sca=None):
    # Never emit snippets, scanner prose, paths, secrets or private URLs.
    sha = run.commit_sha if re.fullmatch(r'[a-f0-9]{40}', run.commit_sha) else 'non communiqué'
    if sca is None and report:
        heading = 'Rapport SAST AUTOKOM'
    else:
        heading = 'Rapport de sécurité AUTOKOM'
    lines = [f'{heading} pour le commit {sha}.', '',
             'Les extraits de code et le journal restent dans AUTOKOM.']
    if report:
        lines += [f'Semgrep a signalé {report["count"]} résultat(s) à examiner.',
                  f'DeepSAST : {report["ai"]["analyzed"]} extrait(s) analysé(s) à titre consultatif.']
        if report['ai']['status'] == 'COMPLETE':
            lines.append('DeepSAST fournit un signal consultatif. Examiner les résultats Semgrep et valider les corrections.')
        if report['ai']['status'] == 'UNAVAILABLE':
            lines.append('Inférence indisponible. Vérifier la configuration DeepSAST.')
        for rule, count in report['rules'].items():
            if rule in SUGGESTIONS:
                lines.append(f'- {rule} ({count}) — {SUGGESTIONS[rule]}')
    if sca:
        lines.append(f'Trivy : {sca["count"]} alerte(s), {sca["remaining"]} restante(s), {sca["secrets"]} secret(s).')
        if sca['remediated']:
            if run.status == 'SUCCEEDED':
                lines.append('La version publiée utilise le lockfile corrigé. Reporter les mises à jour dans GitHub.')
            else:
                lines.append('Le candidat a été corrigé, mais sa mise en ligne n’est pas confirmée.')
        for row in sca['findings']:
            fixed = ', '.join(row['fixed_versions']) or 'aucun correctif connu'
            lines.append(f'- {row["id"]} | {row["package"]} {row["installed"]} | versions correctives signalées : {fixed}')
            if row['applied_versions']:
                lines.append('  Versions observées dans le lockfile corrigé : ' + ', '.join(row['applied_versions']))
    return '\n'.join(lines)


class DeliveryConflict(APIException):
    status_code = 409
    default_detail = 'Action déjà demandée ou résultat incertain. Vérifier la destination avant toute relance.'


def deliver(run, actor, channel, target=0):
    if not settings.AUTOKOM_SECURITY_FEEDBACK_ENABLED:
        raise PermissionDenied('Envoi de rapports non activé sur cette instance.')
    if run.status not in ('FAILED', 'SUCCEEDED', 'BLOCKED') or getattr(run, 'trigger_type', '') == 'ROLLBACK':
        raise ValidationError('Ce rapport ne peut pas être envoyé pour cette publication.')
    report, sca = reports(run)
    if not actionable(report, sca):
        raise ValidationError('Aucun résultat de sécurité à notifier.')
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
        private_repository = repo.get('private') is True
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
    body = message(run, report, sca)
    if channel in ('ISSUE', 'PR') and not private_repository:
        body = ('AUTOKOM a terminé les contrôles de sécurité. Des actions sont à examiner. '
                'Le détail des résultats et les correctifs restent dans le rapport privé du projet AUTOKOM.')
    try:
        number = 0
        if channel == 'EMAIL':
            if send_mail('AUTOKOM · Rapport de sécurité', body, settings.DEFAULT_FROM_EMAIL, [actor.email]) != 1:
                raise DeliveryConflict()
        else:
            resource = '/issues' if channel == 'ISSUE' else f'/issues/{target}/comments'
            data = {'body': body}
            if channel == 'ISSUE':
                data['title'] = 'AUTOKOM · Résultats de sécurité à examiner'
            reply = _request('POST', endpoint + resource, token=token, payload=data)
            number = reply.get('number' if channel == 'ISSUE' else 'id')
            if type(number) is not int or number < 1:
                raise DeliveryConflict()
        SecurityFeedbackAction.objects.filter(pk=action.pk).update(completed_at=timezone.now(), result_number=number)
    except Exception:
        # Do not repeat an external POST/SMTP send with an ambiguous outcome.
        raise DeliveryConflict() from None
    return {'already_sent': False, 'number': number or None}


def notify_terminal(run):
    """Run from the background worker. Automatic delivery uses the project owner."""
    if not settings.AUTOKOM_SECURITY_FEEDBACK_ENABLED or not settings.AUTOKOM_SECURITY_AUTO_NOTIFY:
        return
    if run.trigger_type == 'ROLLBACK' or not actionable(*reports(run)):
        return
    project = run.environment.project
    actor = project.created_by
    if not actor or not actor.is_active or not projects_for_user(actor, write=True).filter(pk=project.pk).exists():
        return
    for channel in ('ISSUE', 'EMAIL'):
        try:
            deliver(run, actor, channel)
        except Exception as exc:
            # Keep failures visible and bounded, never log secrets or repeat
            # an ambiguous external POST. Manual delivery remains available.
            from apps.deployments.runs.worker import add_log
            if not SecurityFeedbackAction.objects.filter(run=run, channel=channel, completed_at__isnull=False).exists():
                add_log(run, f'AUTOKOM_LOG: Notification {channel} non confirmée ({type(exc).__name__}).')
