from django.contrib import admin
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan

@admin.register(DeploymentModeOffer)
class DeploymentModeOfferAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(ResourcePlan)
class ResourcePlanAdmin(admin.ModelAdmin):
    list_per_page = 30

