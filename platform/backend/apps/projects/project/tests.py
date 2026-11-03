"""Tests d'intégration ciblés ; utiliser obligatoirement la base de test MySQL."""
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.accounts.user.models import User
from apps.organizations.models import Organization, OrganizationMembership
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.projects.project.views import ProjectCreateAPIView, ProjectListAPIView
from apps.projects.project.models import Project
from apps.projects.git.services import get_repository


class ProjectAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='creator@example.org', full_name='Créateur', password='not-a-real-password-strong')
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.other = User.objects.create_user(email='other@example.org', full_name='Autre', password='not-a-real-password-strong')
        self.other.is_email_verified = True
        self.other.save(update_fields=['is_email_verified'])
        self.org = Organization.objects.create(owner=self.user, name='Equipe demo', slug='equipe-demo', kind='TEAM')
        OrganizationMembership.objects.create(organization=self.org, user=self.user, role='OWNER')
        DeploymentModeOffer.objects.create(code='DEVOPS', name='DevOps', is_premium=False, is_default=True)
        ResourcePlan.objects.create(code='starter', name='Starter', cpu_millicores=250,
            memory_mb=512, storage_mb=1024, is_default=True, is_premium=False)
        self.factory = APIRequestFactory()

    @patch('apps.projects.project.services.get_repository')
    def test_create_project_and_default_environment(self, mocked_repo):
        mocked_repo.return_value = (
            {'id': 11, 'account': {'id': 33, 'login': 'exampleteam'}},
            {'id': 123, 'full_name': 'exampleteam/myapp', 'html_url': 'https://github.com/exampleteam/myapp',
             'default_branch': 'main'},
        )
        req = self.factory.post('/api/projects/create/', {
            'organization_id': str(self.org.id), 'installation_id': 11,
            'repository_id': 123, 'name': 'MyApp',
        }, format='json')
        force_authenticate(req, user=self.user)
        res = ProjectCreateAPIView.as_view()(req)
        self.assertEqual(res.status_code, 201, res.data)
        project = Project.objects.get(organization=self.org, name='MyApp')
        env = project.environments.get(slug='production')
        self.assertEqual(env.entitlement.mode_offer.code, 'DEVOPS')
        self.assertEqual(env.domains.count(), 1)
        self.assertTrue(env.deployment_configs.first().is_draft)

    def test_other_user_cannot_list_my_projects(self):
        req = self.factory.get('/api/projects/')
        force_authenticate(req, user=self.other)
        res = ProjectListAPIView.as_view()(req)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['projects'], [])
