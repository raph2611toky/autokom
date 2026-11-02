from django.apps import AppConfig


class ConfigurationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.projects.configurations'
    label = 'configurations'
    verbose_name = 'Configurations de déploiement'
