from django.urls import path
from apps.accounts.user import views, github_views, browser_views

app_name = 'client_accounts'
urlpatterns = [
    path('browser/csrf/', browser_views.BrowserCSRFAPIView.as_view(), name='browser-csrf'),
    path('browser/login/', browser_views.BrowserLoginAPIView.as_view(), name='browser-login'),
    path('browser/refresh/', browser_views.BrowserRefreshAPIView.as_view(), name='browser-refresh'),
    path('browser/logout/', browser_views.BrowserLogoutAPIView.as_view(), name='browser-logout'),
    path('browser/github/exchange/', browser_views.BrowserGitHubExchangeAPIView.as_view(), name='browser-github-exchange'),
    path('browser/github/complete/', browser_views.BrowserGitHubCompleteAPIView.as_view(), name='browser-github-complete'),
    path('github/start/', github_views.GitHubLoginStartAPIView.as_view(), name='github-login-start'),
    path('github/link/start/', github_views.GitHubLinkStartAPIView.as_view(), name='github-link-start'),
    path('github/callback/', github_views.GitHubLoginCallbackAPIView.as_view(), name='github-login-callback'),
    path('github/exchange/', github_views.GitHubLoginExchangeAPIView.as_view(), name='github-login-exchange'),
    path('register/', views.RegistrationAPIView.as_view(), name='register'),
    path('email/verify/', views.VerifyEmailAPIView.as_view(), name='email-verify'),
    path('email/resend/', views.ResendEmailAPIView.as_view(), name='email-resend'),
    path('login/', views.LoginAPIView.as_view(), name='login'),
    path('token/refresh/', views.RefreshAPIView.as_view(), name='refresh'),
    path('logout/', views.LogoutAPIView.as_view(), name='logout'),
    path('logout-all/', views.LogoutAllAPIView.as_view(), name='logout-all'),
    path('password/forgot/', views.ForgotPasswordAPIView.as_view(), name='forgot-password'),
    path('password/reset/', views.ResetPasswordAPIView.as_view(), name='reset-password'),
    path('password/change/', views.ChangePasswordAPIView.as_view(), name='change-password'),
    path('me/', views.MeRetrieveAPIView.as_view(), name='me'),
    path('me/update/', views.MeUpdateAPIView.as_view(), name='me-update'),
    path('me/avatar/', views.MeAvatarUploadAPIView.as_view(), name='me-avatar'),
    path('me/permissions/', views.MyPermissionsAPIView.as_view(), name='permissions'),
    path('sessions/', views.SessionsListAPIView.as_view(), name='sessions'),
    path('sessions/<uuid:session_id>/', views.SessionsRevokeAPIView.as_view(), name='sessions-revoke'),
]
