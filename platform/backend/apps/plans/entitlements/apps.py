from django.apps import AppConfig


class PlanEntitlementsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.plans.entitlements'
    label = 'plan_entitlements'
    verbose_name = 'Droits et ressources alloués'
