from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from django.test import SimpleTestCase
from rest_framework.exceptions import NotFound
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.user.models import User
from apps.projects.environments.preview_access import PreviewAccessAPIView, PreviewCredentialsAPIView


EID = UUID('a11e7777-4321-4f00-9070-00a3002001ba')


class PreviewApiTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User(email='client@example.com', is_active=True, is_email_verified=True)
        self.env = MagicMock(pk=EID, wireguard_only=True)
        self.hostname = 'pre.demo.auto.ikom.mg'
        self.env.domains.order_by.return_value = [SimpleNamespace(
            hostname=self.hostname, domain_type='SUBDOMAIN', is_disabled=False,
            status='ACTIVE', tls_status='ACTIVE')]
        self.scope = 'apps.projects.environments.preview_access'

    def request(self, verb, data=None):
        req = getattr(self.factory, verb)('/preview-access/', data, format='json')
        force_authenticate(req, user=self.user)
        return req

    def test_secret_attachment_is_authorized_and_not_cacheable(self):
        with patch(self.scope + '.authorized', return_value=self.env) as auth, \
             patch(self.scope + '.transaction.atomic'), \
             patch(self.scope + '._reject_domain_mutation_during_deployment') as deploy_guard, \
             patch(self.scope + '.request_control', return_value={
                 'issued': True, 'config': '[Interface]\nPrivateKey = test-only\n'}) as request:
            resp = PreviewCredentialsAPIView.as_view()(self.request('post', {}), project_id=EID, environment_id=EID)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('no-store', resp['Cache-Control'])
        self.assertTrue(resp['Content-Disposition'].endswith('.conf"'))
        self.assertIn(b'PrivateKey', resp.content)
        auth.assert_called_once_with(self.user, EID, EID, mutate=True)
        deploy_guard.assert_called_once_with(self.env)
        self.assertEqual(request.call_args.args[1]['operation'], 'issue')

    def test_foreign_project_never_requests_credentials(self):
        with patch(self.scope + '.transaction.atomic'), \
             patch(self.scope + '.get_environment', side_effect=NotFound) as auth, \
             patch(self.scope + '.request_control') as request:
            resp = PreviewCredentialsAPIView.as_view()(self.request('post', {}), project_id=EID, environment_id=EID)
        self.assertEqual(resp.status_code, 404)
        auth.assert_called_once_with(self.user, EID, EID, write=True)
        request.assert_not_called()

    def test_visibility_only_commits_after_broker_confirmation(self):
        self.env.wireguard_only = False
        with patch(self.scope + '.authorized', return_value=self.env), \
             patch(self.scope + '.transaction.atomic'), \
             patch(self.scope + '._reject_domain_mutation_during_deployment'), \
             patch(self.scope + '.request_control', return_value={'private': False}):
            resp = PreviewAccessAPIView.as_view()(self.request('patch', {'private': True}), project_id=EID, environment_id=EID)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.env.wireguard_only)
        self.env.save.assert_not_called()

    def test_production_not_eligible_on_main(self):
        with self.settings(AUTOKOM_PLATFORM_PREPROD=False), \
             patch(self.scope + '.get_environment', return_value=(
                 SimpleNamespace(status='ACTIVE'), SimpleNamespace(kind='PRODUCTION', slug='production', is_active=True))), \
             patch(self.scope + '.request_control') as request:
            resp = PreviewAccessAPIView.as_view()(self.request('get'), project_id=EID, environment_id=EID)
        self.assertEqual(resp.status_code, 404)
        request.assert_not_called()
