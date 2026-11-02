from django.apps import AppConfig


class AuditingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.assistance.auditing'
    label = 'auditing'
    verbose_name = 'Journal d’audit'
