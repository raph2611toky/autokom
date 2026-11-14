from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.user.models import User
from apps.deployments.domains.models import Domain
from apps.deployments.runs.autokom_socket import AutokomControlUnavailable
from apps.organizations.models import Organization, OrganizationMembership
from apps.projects.environments.models import Environment
from apps.projects.project.models import Project
from apps.projects.project.services import retire_project, reserve_domain
from apps.projects.project.views import ProjectDeleteAPIView

BROKER = 'apps.deployments.runs.autokom_socket.request_control'
HOST = 'portfoliolady.auto.ikom.mg'


@override_settings(AUTOKOM_MANAGED_DOMAIN_SUFFIX='auto.ikom.mg')
class ProjectDomainRetirementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='owner@example.org', full_name='Owner', password='test-fixture-not-real-password')
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.org = Organization.objects.create(owner=self.user, name='Equipe', slug='equipe', kind='TEAM')
        OrganizationMembership.objects.create(organization=self.org, user=self.user, role='OWNER')
        self.project = Project.objects.create(organization=self.org, created_by=self.user, name='portfoliolady', slug='portfoliolady')
        self.env = Environment.objects.create(project=self.project, name='Production', slug='production', kind='PRODUCTION')
        self.domain = reserve_domain(self.project, self.env)

    @patch(BROKER, return_value={'offline': True, 'domains_released': True})
    def test_api_delete_frees_managed_and_custom_domains_without_deleting_project(self, control):
        custom = reserve_domain(self.project, self.env, 'www.example.org')
        request = APIRequestFactory().post('/', {'confirm_name': self.project.name}, format='json')
        force_authenticate(request, user=self.user)
        response = ProjectDeleteAPIView.as_view()(request, project_id=self.project.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['domains_released'], 2)
        payload = control.call_args.args[1]
        self.assertEqual(payload['environment_id'], str(self.env.pk))
        self.assertCountEqual(payload['release_domains'], [
            {'hostname': HOST, 'managed': True}, {'hostname': custom.hostname, 'managed': False},
        ])
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'DELETED')
        self.assertEqual(self.project.name, 'portfoliolady')
        self.assertTrue(Environment.objects.filter(pk=self.env.pk).exists())
        replacement = Project.objects.create(organization=self.org, created_by=self.user, name='portfoliolady', slug='portfoliolady')
        env = Environment.objects.create(project=replacement, name='Production', slug='production', kind='PRODUCTION')
        self.assertEqual(reserve_domain(replacement, env).hostname, HOST)
        self.assertEqual(reserve_domain(replacement, env, custom.hostname).hostname, custom.hostname)

    @patch(BROKER, return_value={'offline': True})
    def test_incomplete_or_old_broker_keeps_reservation(self, control):
        with self.assertRaises(ValidationError):
            retire_project(self.project.pk)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'ACTIVE')
        self.assertTrue(Domain.objects.filter(hostname=HOST).exists())

    @patch(BROKER)
    def test_broker_failure_keeps_reservation(self, control):
        error = AutokomControlUnavailable()
        error.safe_code = 'AUTOKOM_DOMAIN_DNS_UNOWNED'
        control.side_effect = error
        with self.assertRaisesRegex(ValidationError, 'ancien enregistrement DNS'):
            retire_project(self.project.pk)
        self.assertTrue(Domain.objects.filter(hostname=HOST).exists())

    @patch(BROKER, return_value={'offline': True, 'domains_released': True})
    def test_historical_cleanup_is_dry_run_by_default_and_idempotent(self, control):
        Project.objects.filter(pk=self.project.pk).update(status='DELETED')
        output = StringIO()
        call_command('autokom_release_deleted_domains', hostname=HOST, stdout=output)
        self.assertIn('Aperçu', output.getvalue())
        control.assert_not_called()
        call_command('autokom_release_deleted_domains', hostname=HOST, apply=True, stdout=output)
        self.assertFalse(Domain.objects.filter(hostname=HOST).exists())
        call_command('autokom_release_deleted_domains', hostname=HOST, apply=True, stdout=output)
        control.assert_called_once()

    @patch(BROKER)
    def test_historical_cleanup_never_touches_active_or_archived_project(self, control):
        for status in ['ACTIVE', 'ARCHIVED']:
            Project.objects.filter(pk=self.project.pk).update(status=status)
            with self.assertRaises(CommandError):
                call_command('autokom_release_deleted_domains', hostname=HOST, apply=True)
        control.assert_not_called()
