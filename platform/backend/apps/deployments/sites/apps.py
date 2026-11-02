from django.apps import AppConfig


class SitesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments.sites'
    label = 'sites'
    verbose_name = 'Applications hébergées'
