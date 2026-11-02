from django.apps import AppConfig


class InfrastructureConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.infrastructure.tools'
    label = 'infrastructure'
    verbose_name = 'Outils et capacités'
