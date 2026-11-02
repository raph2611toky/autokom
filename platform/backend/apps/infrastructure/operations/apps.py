from django.apps import AppConfig


class OperationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.infrastructure.operations'
    label = 'operations'
    verbose_name = 'Opérations asynchrones'
