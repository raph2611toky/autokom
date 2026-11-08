from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import include, path
from django.views.decorators.cache import never_cache
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAdminUser

from config.views import SwaggerLoginPageAPIView, SwaggerLoginSubmitAPIView, SwaggerLogoutAPIView
from config.health import PlatformHealthAPIView
from apps.deployments.runs.views import RunnerManifestAPIView, RunnerSourceArchiveAPIView

# Only client APIs enter the generated schema; no internal/admin endpoints.
client_api_patterns = [
    path('api/health/', PlatformHealthAPIView.as_view(), name='platform-health'),
    path('api/auth/', include('apps.accounts.user.urls')),
    path('api/projects/git/', include('apps.projects.git.urls')),
    path('api/projects/', include('apps.projects.environments.urls')),
    path('api/projects/', include('apps.projects.configurations.urls')),
    path('api/projects/', include('apps.projects.secrets.urls')),
    path('api/projects/', include('apps.projects.project.urls')),
    path('api/projects/', include('apps.deployments.runs.urls')),
    path('api/projects/', include('apps.deployments.domains.urls')),
]
schema_view = get_schema_view(
    openapi.Info(
        title='AUTOKOM Platform — API clients',
        default_version='v1',
        description='API utilisateurs AUTOKOM, distincte des comptes administrateur Django.',
    ),
    public=True,  # Tous les endpoints clients sont documentés ; accès au schéma réservé aux admins.
    patterns=client_api_patterns,
    authentication_classes=(SessionAuthentication,),
    permission_classes=(IsAdminUser,),
)

def admin_documentation(view):
    return never_cache(staff_member_required(view, login_url='/docs/login/'))

urlpatterns = [
    path('admin/', admin.site.urls),
    path('docs/login/', SwaggerLoginPageAPIView.as_view(), name='swagger-login'),
    path('docs/login/submit/', SwaggerLoginSubmitAPIView.as_view(), name='swagger-login-submit'),
    path('docs/logout/', SwaggerLogoutAPIView.as_view(), name='swagger-logout'),
    *client_api_patterns,
    path('api/internal/runner/deployments/<uuid:run_id>/manifest/',
         RunnerManifestAPIView.as_view(),
         name='internal-runner-manifest'),
    path('api/internal/runner/deployments/<uuid:run_id>/source/',
         RunnerSourceArchiveAPIView.as_view(),
         name='internal-runner-source'),
    path('api/docs/', admin_documentation(schema_view.with_ui('swagger', cache_timeout=0)), name='swagger-ui'),
    path('api/schema.json', admin_documentation(schema_view.without_ui(cache_timeout=0)), name='swagger-schema'),
    path('api/redoc/', admin_documentation(schema_view.with_ui('redoc', cache_timeout=0)), name='swagger-redoc'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = 'config.views.handle_404'
