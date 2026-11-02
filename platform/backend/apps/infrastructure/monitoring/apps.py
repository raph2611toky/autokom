from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.infrastructure.monitoring'
    label = 'monitoring'
    verbose_name = 'Supervision et alertes'
