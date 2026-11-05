"""Regression: les domaines créés avec un projet doivent être vérifiables."""
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from apps.projects.project.services import reserve_domain
from apps.deployments.domains.services import _challenge


class ProjectDomainProofTests(SimpleTestCase):
    @override_settings(AUTOKOM_BASE_DOMAIN='auto.ikom.mg', SECRET_KEY='test-key-for-domain')
    @patch('apps.projects.project.services.Domain')
    def test_initial_project_domain_has_verification_proof(self, model):
        model.Type.SUBDOMAIN = 'SUBDOMAIN'
        model.Status.PENDING = 'PENDING'
        model.objects.filter.return_value.exists.return_value = False
        domain = SimpleNamespace(pk='11111111-1111-4111-8111-111111111111', verification_token_hash='', save=lambda **kw: None)
        model.objects.create.return_value = domain
        project = SimpleNamespace(slug='site-test', organization=SimpleNamespace())
        env = SimpleNamespace()
        result = reserve_domain(project, env)
        self.assertEqual(result.verification_token_hash, hashlib.sha256(_challenge(result).encode()).hexdigest())
        self.assertEqual(model.objects.create.call_args.kwargs['hostname'], 'site-test.auto.ikom.mg')
