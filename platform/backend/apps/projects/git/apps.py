from django.apps import AppConfig


class GitIntegrationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.projects.git'
    label = 'integrations'
    verbose_name = 'Dépôts et événements Git'
