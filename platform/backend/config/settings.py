"""AUTOKOM Platform — configuration unique, MySQL uniquement."""
from datetime import timedelta
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def env_bool(key, default=False):
    return os.getenv(key, str(default)).strip().lower() in {'1', 'true', 'yes', 'on'}


def env_csv(key, default=''):
    return [value.strip() for value in os.getenv(key, default).split(',') if value.strip()]


DEBUG = env_bool('DEBUG', False)
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '').strip()
if not SECRET_KEY or SECRET_KEY.startswith('CHANGE_ME'):
    if DEBUG:
        SECRET_KEY = 'autokom-local-debug-not-for-production-only'
    else:
        raise ImproperlyConfigured('Définir DJANGO_SECRET_KEY en production.')
ALLOWED_HOSTS = env_csv('ALLOWED_HOSTS', '127.0.0.1,localhost')

# Aucun backend DB alternatif : ni SQLite ni PostgreSQL, même avec DEBUG=True.
if os.getenv('DB_ENGINE', 'mysql').lower() != 'mysql':
    raise ImproperlyConfigured('AUTOKOM Platform exige MySQL pour tous les environnements.')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
if not DEBUG and (not DB_PASSWORD or DB_PASSWORD.startswith('CHANGE_ME')):
    raise ImproperlyConfigured('Configurer DB_PASSWORD en production.')
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.getenv('DB_NAME', 'autokom_platform'),
        'USER': os.getenv('DB_USER', 'autokom'),
        'PASSWORD': DB_PASSWORD,
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '3306'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'charset': 'utf8mb4',
            'isolation_level': 'read committed',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        'TEST': {'NAME': os.getenv('DB_TEST_NAME', 'test_autokom_platform')},
    }
}

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]
THIRD_PARTY_APPS = ['rest_framework', 'drf_yasg', 'corsheaders']
LOCAL_APPS = [
    'apps.accounts.admin.apps.AdministratorsConfig',
    'apps.accounts.user.apps.ClientsConfig',
    'apps.organizations.apps.OrganizationsConfig',
    'apps.projects.git.apps.GitIntegrationsConfig',
    'apps.projects.project.apps.ProjectsConfig',
    'apps.projects.environments.apps.EnvironmentsConfig',
    'apps.projects.configurations.apps.ConfigurationsConfig',
    'apps.projects.secrets.apps.SecretsConfig',
    'apps.deployments.runs.apps.DeploymentsConfig',
    'apps.deployments.releases.apps.ReleasesConfig',
    'apps.deployments.sites.apps.SitesConfig',
    'apps.deployments.domains.apps.DomainsConfig',
    'apps.plans.catalog.apps.PlanCatalogConfig',
    'apps.plans.entitlements.apps.PlanEntitlementsConfig',
    'apps.assistance.ai.apps.AssistantConfig',
    'apps.assistance.notifications.apps.NotificationsConfig',
    'apps.assistance.auditing.apps.AuditingConfig',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
AUTH_USER_MODEL = 'administrators.Administrator'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['apps.accounts.user.authentication.ClientJWTAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'EXCEPTION_HANDLER': 'helpers.errors.global_api_exception_handler',
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/hour', 'login': '10/hour', 'registration': '5/hour',
        'reset': '6/hour', 'refresh': '60/hour', 'github_oauth': '20/hour',
    },
}
SWAGGER_SETTINGS = {
    'USE_SESSION_AUTH': False,
    'SECURITY_DEFINITIONS': {
        'ClientBearer': {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': 'Saisir : Bearer <jeton_client> (JWT utilisateur, pas session Django Admin).',
        },
    },
    'VALIDATOR_URL': None,
    'DEFAULT_MODEL_RENDERING': 'example',
}
CORS_ALLOWED_ORIGINS = env_csv('CORS_ALLOWED_ORIGINS')
CORS_ALLOW_CREDENTIALS = False
CSRF_TRUSTED_ORIGINS = env_csv('CSRF_TRUSTED_ORIGINS')

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Indian/Antananarivo'
USE_TZ = True
USE_I18N = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'statics']
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
if not DEBUG and EMAIL_BACKEND == 'django.core.mail.backends.console.EmailBackend':
    raise ImproperlyConfigured('Configurer un backend SMTP en production.')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'AUTOKOM <no-reply@example.invalid>')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173').rstrip('/')

CLIENT_JWT_SIGNING_KEY = os.getenv('CLIENT_JWT_SIGNING_KEY', '')
if not CLIENT_JWT_SIGNING_KEY or CLIENT_JWT_SIGNING_KEY.startswith('CHANGE_ME'):
    if DEBUG:
        CLIENT_JWT_SIGNING_KEY = SECRET_KEY + '-client-only-dev-key'
    else:
        raise ImproperlyConfigured('Définir CLIENT_JWT_SIGNING_KEY en production.')
CLIENT_JWT_ISSUER = os.getenv('CLIENT_JWT_ISSUER', 'autokom-platform')
CLIENT_JWT_AUDIENCE = os.getenv('CLIENT_JWT_AUDIENCE', 'autokom-clients')
CLIENT_ACCESS_LIFETIME = timedelta(minutes=int(os.getenv('CLIENT_ACCESS_MINUTES', '15')))
CLIENT_REFRESH_LIFETIME = timedelta(days=int(os.getenv('CLIENT_REFRESH_DAYS', '7')))
EMAIL_VERIFICATION_LIFETIME = timedelta(hours=24)
PASSWORD_RESET_LIFETIME = timedelta(minutes=30)

LOGIN_URL = '/docs/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', not DEBUG)
if env_bool('TRUST_PROXY_SSL_HEADER', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# GitHub App user authorization. Configurer les valeurs réelles dans .env.
GITHUB_APP_CLIENT_ID = os.getenv('GITHUB_APP_CLIENT_ID', '').strip()
GITHUB_APP_CLIENT_SECRET = os.getenv('GITHUB_APP_CLIENT_SECRET', '').strip()
GITHUB_APP_SLUG = os.getenv('GITHUB_APP_SLUG', '').strip()
GITHUB_APP_CALLBACK_URL = os.getenv('GITHUB_APP_CALLBACK_URL', '').strip()
GITHUB_WEBHOOK_SECRET = os.getenv('GITHUB_WEBHOOK_SECRET', '').strip()
AUTOKOM_FIELD_ENCRYPTION_KEY = os.getenv('AUTOKOM_FIELD_ENCRYPTION_KEY', '').strip()
AUTOKOM_BASE_DOMAIN = os.getenv('AUTOKOM_BASE_DOMAIN', 'apps.autokom.example').lower().strip('.')
GITHUB_API_VERSION = os.getenv('GITHUB_API_VERSION', '2026-03-10')
