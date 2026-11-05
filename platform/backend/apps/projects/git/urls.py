from django.urls import path
from apps.projects.git.views import (
    GitHubStartAPIView, GitHubStatusAPIView, GitHubCallbackAPIView,
    GitHubDisconnectAPIView, GitHubInstallationsAPIView, GitHubRepositoriesAPIView,
    GitHubBranchesAPIView, GitHubDirectoriesAPIView, GitHubWebhookAPIView,
)

app_name = 'project_git'
urlpatterns = [
    path('github/start/', GitHubStartAPIView.as_view(), name='github-start'),
    path('github/status/', GitHubStatusAPIView.as_view(), name='github-status'),
    path('github/callback/', GitHubCallbackAPIView.as_view(), name='github-callback'),
    path('github/disconnect/', GitHubDisconnectAPIView.as_view(), name='github-disconnect'),
    path('github/installations/', GitHubInstallationsAPIView.as_view(), name='github-installations'),
    path('github/repositories/', GitHubRepositoriesAPIView.as_view(), name='github-repositories'),
    path('github/branches/', GitHubBranchesAPIView.as_view(), name='github-branches'),
    path('github/directories/', GitHubDirectoriesAPIView.as_view(), name='github-directories'),
    path('github/webhook/', GitHubWebhookAPIView.as_view(), name='github-webhook'),
]
