"""Security summaries are bounded and external delivery is opt-in."""
import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.deployments.runs.security_feedback import deliver, message, normalize_report, correction_steps, public_issue_message, actionable
from apps.deployments.runs.security_views import verify_report_signature
from apps.projects.project.services import reserve_domain


REPORT = {'count': 1, 'rules': {'approved.rule': 1}, 'ai': {
    'status': 'COMPLETE', 'analyzed': 1, 'classes': {'1': 1}}}


class SecurityFeedbackTests(SimpleTestCase):
    def test_normalize_never_persists_raw_source(self):
        value = {**REPORT, 'source': 'do not disclose'}
        clean = normalize_report(value)
        self.assertEqual(clean, REPORT)
        body = message(SimpleNamespace(commit_sha='a' * 40), clean)
        self.assertIn('1 résultat(s)', body)
        self.assertNotIn('do not disclose', body)
        self.assertNotIn('approved.rule', body)

    def test_clean_semgrep_run_can_store_independent_inference(self):
        value = {'count': 0, 'rules': {}, 'ai': {
            'status': 'COMPLETE', 'analyzed': 2, 'classes': {'1': 2}, 'scope': 'SOURCE'}}
        self.assertEqual(normalize_report(value), value)
        value['ai']['scope'] = 'SEMGREP'
        with self.assertRaises(ValidationError):
            normalize_report(value)

    @override_settings(FRONTEND_URL='https://auto.ikom.mg')
    def test_public_issue_has_actionable_steps_but_no_target_details(self):
        run = SimpleNamespace(status='FAILED',pk='run',environment=SimpleNamespace(project_id='project'))
        sca = {'count':1, 'npm':True,'overrides':{'dep@1.0.0':'1.0.1'},'findings':[{'package':'dep'}]}
        self.assertIn('npm pkg set "overrides[dep@1.0.0]=1.0.1"',correction_steps(sca))
        body = public_issue_message(run,REPORT,sca)
        self.assertIn('https://auto.ikom.mg/projects/project/deployments/run',body)
        self.assertIn('npm audit fix',body)
        self.assertNotIn('dep@1.0.0',body)
        self.assertNotIn('approved.rule',body)
        self.assertTrue(actionable(REPORT,None))
        self.assertFalse(correction_steps({'npm':False,'count':1}))

    def test_reject_bad_counts_and_bad_rule(self):
        for value in ({**REPORT, 'count': 0}, {**REPORT, 'rules': {'abc\nxyz': 1}},
                      {**REPORT, 'ai': {'status': 'COMPLETE', 'analyzed': 0}}):
            with self.assertRaises(ValidationError):
                normalize_report(value)

    def test_report_sast_technical_failure_without_trivy(self):
        value = normalize_report({'count':0,'rules':{},'ai':{'status':'DISABLED','analyzed':0},'scan_failed':True})
        self.assertTrue(actionable(value,None))
        self.assertIn('sudo autokom runtime doctor',message(SimpleNamespace(commit_sha='a'*40),value))

    @override_settings(AUTOKOM_SECURITY_FEEDBACK_ENABLED=False)
    def test_no_side_effect_by_default(self):
        with patch('apps.deployments.runs.security_feedback.send_mail') as smtp:
            with self.assertRaises(PermissionDenied):
                deliver(None, None, 'EMAIL')
        smtp.assert_not_called()

    @override_settings(AUTOKOM_SECURITY_FEEDBACK_ENABLED=True, AUTOKOM_SECURITY_EMAIL_RECIPIENTS=frozenset())
    def test_email_must_be_verified_and_allowlisted(self):
        with self.assertRaises(PermissionDenied):
            deliver(SimpleNamespace(status='FAILED', sast_report=REPORT),
                    SimpleNamespace(is_email_verified=True, email='operator@example.org'), 'EMAIL')

    @override_settings(AUTOKOM_RUNNER_SHARED_KEY='x' * 48)
    def test_report_signature_is_bound_to_body_and_run(self):
        epoch, run_id, payload = str(int(time.time())), 'sample', b'{}'
        digest = hashlib.sha256(payload).hexdigest()
        valid = hmac.new(b'x' * 48, f'autokom-sast:v1:{epoch}:{run_id}:{digest}'.encode(),
                         hashlib.sha256).hexdigest()
        verify_report_signature(run_id, epoch, valid, payload)
        with self.assertRaises(PermissionDenied):
            verify_report_signature(run_id, epoch, valid, b'{"count":0}')


class StageDomainTests(SimpleTestCase):
    @override_settings(AUTOKOM_MANAGED_DOMAIN_SUFFIX='auto.ikom.mg', SECRET_KEY='test-key')
    @patch('apps.projects.project.services.Domain')
    def test_staging_uses_actual_production_alias(self, model):
        model.Type.SUBDOMAIN = 'SUBDOMAIN'
        model.Status.PENDING = 'PENDING'
        query = model.objects.filter.return_value
        query.order_by.return_value.first.return_value = SimpleNamespace(hostname='renamed.auto.ikom.mg')
        query.exists.return_value = False
        domain = SimpleNamespace(pk='test-id', save=lambda **kw: None)
        model.objects.create.return_value = domain
        project = SimpleNamespace(slug='original', organization=object())
        reserve_domain(project, SimpleNamespace(kind='STAGING'))
        self.assertEqual(model.objects.create.call_args.kwargs['hostname'], 'pre.renamed.auto.ikom.mg')
