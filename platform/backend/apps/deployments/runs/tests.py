"""Run under an isolated Django test configuration (never on production MySQL)."""
import hashlib
import hmac
import time
import uuid
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import PermissionDenied
from apps.deployments.runs.runner_manifest import check_runner_signature
from apps.deployments.runs.worker import redact
from apps.deployments.runs.jenkins import verify_settings, JenkinsUnavailable


class RunnerSignatureTests(SimpleTestCase):
    @override_settings(AUTOKOM_RUNNER_SHARED_KEY='k' * 48)
    def test_signed_runner_request(self):
        identity = uuid.uuid4()
        now = str(int(time.time()))
        signature = hmac.new(b'k' * 48,
            f'autokom-runner:v1:{now}:{identity}'.encode(),hashlib.sha256).hexdigest()
        check_runner_signature(identity, now, signature)
        with self.assertRaises(PermissionDenied):
            check_runner_signature(identity, now, '0' * 64)

    @override_settings(AUTOKOM_RUNNER_SHARED_KEY='k' * 48)
    def test_expired_signature_rejected(self):
        with self.assertRaises(PermissionDenied):
            check_runner_signature(uuid.uuid4(),str(int(time.time())-120),'0'*64)


class JenkinsSettingsTests(SimpleTestCase):
    @override_settings(AUTOKOM_JENKINS_URL='https://ci.example.invalid',
        AUTOKOM_JENKINS_JOB='autokom-client-deploy',AUTOKOM_JENKINS_USER='svc',
        AUTOKOM_JENKINS_API_TOKEN='secret')
    def test_https_server_allowed(self):
        self.assertEqual(('https://ci.example.invalid','autokom-client-deploy'), verify_settings())

    @override_settings(AUTOKOM_JENKINS_URL='http://ci.example.invalid',
        AUTOKOM_JENKINS_JOB='autokom-client-deploy',AUTOKOM_JENKINS_USER='svc',
        AUTOKOM_JENKINS_API_TOKEN='secret')
    def test_public_plain_http_rejected(self):
        with self.assertRaises(JenkinsUnavailable):
            verify_settings()

    def test_redaction(self):
        self.assertNotIn('ghs_ABCdef123456789012345',redact('AUTOKOM_LOG: token=ghs_ABCdef123456789012345'))
