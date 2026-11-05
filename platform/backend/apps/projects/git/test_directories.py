"""Tests d'autorisation du parcours des dossiers GitHub (API Django)."""
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.accounts.user.models import User
from apps.projects.git.views import GitHubDirectoriesAPIView


class GitHubDirectoryAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='folders@example.org', full_name='Dossiers Git', password='ComplexePass!5678')
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.factory = APIRequestFactory()

    def request(self, **params):
        request = self.factory.get('/api/projects/git/github/directories/', params)
        force_authenticate(request, user=self.user)
        return GitHubDirectoriesAPIView.as_view()(request)

    @patch('apps.projects.git.views.get_repository')
    def test_refuse_path_traversal_before_calling_github(self, get_repo):
        res = self.request(installation_id=7, repository_id=45, branch='main', path='../secrets')
        self.assertEqual(res.status_code, 400)
        get_repo.assert_not_called()

    @patch('apps.projects.git.views.get_user_token', return_value='mock-github-user-token')
    @patch('apps.projects.git.views._request')
    @patch('apps.projects.git.views.get_branches')
    @patch('apps.projects.git.views.get_repository')
    def test_returns_only_authorized_directories(self, get_repo, get_branches, github_request, get_token):
        get_repo.return_value = ({'id': 7}, {'full_name': 'allowed/repo'})
        get_branches.return_value = ({}, [{'name': 'main'}])
        github_request.return_value = [
            {'name': 'src', 'path': 'src', 'type': 'dir'},
            {'name': '.env', 'path': '.env', 'type': 'file'},
        ]
        res = self.request(installation_id=7, repository_id=45, branch='main')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['directories'], [{'name': 'src', 'path': 'src', 'type': 'dir'}])
        self.assertIn('/repos/allowed/repo/contents?ref=main', github_request.call_args.args[1])

    @patch('apps.projects.git.views._request')
    @patch('apps.projects.git.views.get_branches')
    @patch('apps.projects.git.views.get_repository')
    def test_denies_unknown_branch_without_listing_contents(self, get_repo, get_branches, github_request):
        get_repo.return_value = ({'id': 7}, {'full_name': 'allowed/repo'})
        get_branches.return_value = ({}, [{'name': 'main'}])
        res = self.request(installation_id=7, repository_id=45, branch='unapproved')
        self.assertEqual(res.status_code, 400)
        github_request.assert_not_called()
