from django.apps import AppConfig


class PoliciesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.infrastructure.policies'
    label = 'policies'
    verbose_name = 'Politiques de pipelines'
