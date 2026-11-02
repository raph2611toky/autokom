from django.apps import AppConfig


class ReleasesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments.releases'
    label = 'releases'
    verbose_name = 'Artefacts et releases'
