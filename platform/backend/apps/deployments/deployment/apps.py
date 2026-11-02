from django.apps import AppConfig


class DeploymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments.deployment'
    label = 'deployments'
    verbose_name = 'Exécutions de déploiement'
