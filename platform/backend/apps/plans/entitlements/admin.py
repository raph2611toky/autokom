from django.contrib import admin
from apps.plans.entitlements.models import ApplicationEntitlement

@admin.register(ApplicationEntitlement)
class ApplicationEntitlementAdmin(admin.ModelAdmin):
    list_per_page = 30

