from django.apps import AppConfig


class ResourcesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.infrastructure.resources'
    label = 'resources'
    verbose_name = 'Ressources et quotas'
