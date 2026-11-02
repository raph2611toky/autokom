from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.projects.integrations'
    label = 'integrations'
    verbose_name = 'Intégrations Git'
