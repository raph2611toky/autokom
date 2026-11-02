from django.apps import AppConfig


class PlanCatalogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.plans.catalog'
    label = 'plan_catalog'
    verbose_name = 'Catalogue des offres'
