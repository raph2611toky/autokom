from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.user.models import User
from apps.deployments.domains.models import Domain
from apps.deployments.runs.autokom_socket import AutokomControlUnavailable
from apps.organizations.models import Organization
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.projects.environments.models import Environment
from apps.projects.project.models import Project
from apps.projects.project.views import ProjectCreateAPIView, ProjectNamePreviewAPIView


CONTROL = 'apps.projects.project.naming.request_control'
REPO = 'apps.projects.project.services.get_repository'
HOST = 'portfolio.auto.ikom.mg'


@override_settings(AUTOKOM_BASE_DOMAIN='ikom.mg', AUTOKOM_MANAGED_DOMAIN_SUFFIX='auto.ikom.mg',
                   AUTOKOM_PLATFORM_PREPROD=False)
class ProjectNamingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='owner@example.org', full_name='Owner', password='test-password-not-live')
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.organization = Organization.objects.create(owner=self.user, name='Equipe', slug='equipe', kind='TEAM')
        DeploymentModeOffer.objects.create(code='DEVOPS', name='DevOps', is_premium=False, is_default=True)
        ResourcePlan.objects.create(code='starter', name='Starter', cpu_millicores=250,
                                    memory_mb=512, storage_mb=1024, is_default=True, is_premium=False)
        mock_repo = patch(REPO, return_value=(
            {'id': 11, 'account': {'id': 33, 'login': 'exampleteam'}},
            {'id': 123, 'full_name': 'exampleteam/portfolio', 'html_url': 'https://github.com/exampleteam/portfolio',
             'default_branch': 'main'},
        ))
        self.addCleanup(mock_repo.stop)
        self.repo = mock_repo.start()
        self.factory = APIRequestFactory()

    def post(self, view, *, name='Portfolio', user=None):
        request = self.factory.post('/', {
            'organization_id': str(self.organization.pk), 'name': name,
            'installation_id': 11, 'repository_id': 123,
        }, format='json')
        force_authenticate(request, user=user or self.user)
        return view.as_view()(request)

    def reserve_elsewhere(self, hostname, *, slug='other', status='ACTIVE', organization=None):
        organization = organization or self.organization
        project = Project.objects.create(organization=organization, created_by=self.user, name='historique',
                                         slug=slug, status=status)
        env = Environment.objects.create(project=project, name='Production', slug='production', kind='PRODUCTION')
        return Domain.objects.create(organization=organization, environment=env, hostname=hostname, domain_type='SUBDOMAIN')

    @patch(CONTROL, return_value={'available': True})
    def test_free_name_preview_is_read_only_and_matches_creation(self, control):
        preview = self.post(ProjectNamePreviewAPIView)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data['data']['hostname'], HOST)
        self.assertFalse(preview.data['data']['suffixed'])
        self.repo.assert_not_called()
        self.assertFalse(Project.objects.exists())
        self.assertFalse(Domain.objects.exists())
        response = self.post(ProjectCreateAPIView)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['data']['domain'], HOST)
        self.assertEqual(control.call_count, 2)

    @patch(CONTROL)
    def test_dns_record_without_a_project_gets_new_suffix_and_is_not_touched(self, control):
        control.side_effect = lambda action, params, timeout: {'available': params['hostname'] != HOST}
        preview = self.post(ProjectNamePreviewAPIView)
        suggestion = preview.data['data']['hostname']
        self.assertRegex(suggestion, r'^portfolio-[a-f0-9]{8}\.auto\.ikom\.mg$')
        response = self.post(ProjectCreateAPIView)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['data']['domain'], suggestion)
        self.assertTrue(all(call.args[0] == 'domain.available' for call in control.call_args_list))

    @patch(CONTROL, return_value={'available': True})
    def test_historical_project_slug_with_no_domain_still_gets_suffix(self, control):
        Project.objects.create(organization=self.organization, created_by=self.user, name='Portfolio',
                               slug='portfolio', status='DELETED')
        response = self.post(ProjectCreateAPIView)
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['data']
        self.assertEqual(data['project']['name'], 'Portfolio')
        self.assertRegex(data['project']['slug'], r'^portfolio-[a-f0-9]{8}$')
        self.assertEqual(data['domain'], data['project']['slug'] + '.auto.ikom.mg')
        self.assertEqual(Project.objects.count(), 2)

    @patch(CONTROL, return_value={'available': True})
    def test_global_domain_and_candidate_collisions_retry_without_taking_ownership(self, control):
        other = Organization.objects.create(owner=self.user, name='Autre', slug='autre', kind='TEAM')
        existing = self.reserve_elsewhere(HOST, organization=other)
        first = self.post(ProjectNamePreviewAPIView).data['data']['hostname']
        taken = self.reserve_elsewhere(first, slug='another')
        response = self.post(ProjectCreateAPIView)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn(response.data['data']['domain'], [HOST, first])
        self.assertTrue(Domain.objects.filter(pk=existing.pk, organization=other).exists())
        self.assertTrue(Domain.objects.filter(pk=taken.pk).exists())

    @patch(CONTROL, return_value={'available': True})
    def test_db_race_retries_with_a_new_verified_address(self, control):
        from apps.projects.project.services import _store_project
        with patch('apps.projects.project.services._store_project') as store:
            def write(*args, address, **kwargs):
                if address.hostname == HOST:
                    self.reserve_elsewhere(HOST)
                    raise IntegrityError('simulated concurrent reservation')
                return _store_project(*args, address=address, **kwargs)
            store.side_effect = write
            response = self.post(ProjectCreateAPIView)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data['data']['domain'], HOST)
        self.assertEqual(store.call_count, 2)

    @patch(CONTROL, side_effect=AutokomControlUnavailable)
    def test_provider_failure_is_an_error_before_creation(self, control):
        for view in (ProjectNamePreviewAPIView, ProjectCreateAPIView):
            response = self.post(view)
            self.assertEqual(response.status_code, 503, response.data)
        self.assertFalse(Project.objects.exists())
        self.assertFalse(Domain.objects.exists())

    @patch(CONTROL, return_value={'available': True})
    def test_reserved_label_and_long_project_name(self, control):
        self.assertRegex(self.post(ProjectNamePreviewAPIView, name='pre').data['data']['hostname'],
                         r'^pre-[a-f0-9]{8}\.auto\.ikom\.mg$')
        label = self.post(ProjectNamePreviewAPIView, name='A' * 160).data['data']['hostname'].split('.')[0]
        self.assertEqual(len(label), 63)

    @override_settings(AUTOKOM_PLATFORM_PREPROD=True)
    @patch(CONTROL)
    def test_preprod_keeps_existing_exact_name_and_allowlist_workflow(self, control):
        preview = self.post(ProjectNamePreviewAPIView)
        self.assertEqual(preview.data['data'], {'enabled': False})
        result = self.post(ProjectCreateAPIView)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['data']['domain'], HOST)
        control.assert_not_called()

    @patch(CONTROL)
    def test_no_cross_org_preview(self, control):
        other = User.objects.create_user(email='uninvited@example.org', full_name='Uninvited', password='unused-password')
        other.is_email_verified = True
        other.save(update_fields=['is_email_verified'])
        self.assertEqual(self.post(ProjectNamePreviewAPIView, user=other).status_code, 404)
        control.assert_not_called()
