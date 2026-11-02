from django.apps import AppConfig


class AdministratorsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts.admin'
    label = 'administrators'
    verbose_name = 'Administration système'
