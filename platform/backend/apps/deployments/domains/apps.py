from django.apps import AppConfig


class DomainsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments.domains'
    label = 'domains'
    verbose_name = 'Domaines publics'
