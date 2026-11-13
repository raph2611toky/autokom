"""Dedicated deployment settings. Never point staging at production services."""

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403

HOST = 'pre.auto.ikom.mg'
if (DEBUG or DATABASES['default']['NAME'] != 'autokom_preprod'
        or DATABASES['default']['USER'] != 'autokom_preprod'
        or DATABASES['default']['HOST'] != '127.0.0.1'
        or DATABASES['default']['TEST']['NAME'] != 'test_autokom_preprod'
        or AUTOKOM_CONTROL_SOCKET != '/run/autokom-preprod/platform-control.sock'
        or FRONTEND_URL != 'https://' + HOST
        or ALLOWED_HOSTS != [HOST]
        or AUTOKOM_MANAGED_DOMAIN_SUFFIX != 'auto.ikom.mg'
        or len(AUTOKOM_RUNNER_SHARED_KEY) < 32):
    raise ImproperlyConfigured('Configuration isolée de préproduction obligatoire.')

for url in (GITHUB_APP_CALLBACK_URL, GITHUB_AUTH_CALLBACK_URL):
    if url and not url.startswith('https://' + HOST + '/'):
        raise ImproperlyConfigured('Callback GitHub hors préproduction.')

AUTOKOM_PLATFORM_PREPROD = True
AUTOKOM_HA_CLUSTER_READY = False
AUTOKOM_IMAGE_REGISTRY = ''
AUTOKOM_RUNTIME_LOGS_ENABLED = False
AUTOKOM_PROMETHEUS_URL = ''
AUTOKOM_LOKI_URL = ''
AUTOKOM_BILLING_SANDBOX_ENABLED = False
CLIENT_JWT_ISSUER = 'autokom-preprod'
CLIENT_JWT_AUDIENCE = 'autokom-preprod-clients'
SESSION_COOKIE_NAME = 'autokom_preprod_sessionid'
CSRF_COOKIE_NAME = 'csrftoken'  # Host-only; frontend expects this name.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
CSRF_TRUSTED_ORIGINS = ['https://' + HOST]
CORS_ALLOWED_ORIGINS = []
STATIC_ROOT = Path('/var/lib/autokom-preprod/static')
MEDIA_ROOT = Path('/var/lib/autokom-preprod/media')
AUTOKOM_PREPROD_EMAIL_ENABLED = env_bool('AUTOKOM_PREPROD_EMAIL_ENABLED', False)
if not AUTOKOM_PREPROD_EMAIL_ENABLED:
    EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
    AUTOKOM_SECURITY_AUTO_NOTIFY = False
    AUTOKOM_SECURITY_EMAIL_RECIPIENTS = frozenset()
elif EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend' or not EMAIL_HOST:
    raise ImproperlyConfigured('SMTP de préproduction non configuré.')
