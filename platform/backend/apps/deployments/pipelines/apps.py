from django.apps import AppConfig


class PipelinesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments.pipelines'
    label = 'pipelines'
    verbose_name = 'Pipelines et journaux'
