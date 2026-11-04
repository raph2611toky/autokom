from django.contrib import admin
from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption

@admin.register(ApplicationEntitlement)
class ApplicationEntitlementAdmin(admin.ModelAdmin):
    list_per_page = 30



@admin.register(HighAvailabilityOption)
class HighAvailabilityOptionAdmin(admin.ModelAdmin):
    list_display = ('environment', 'status', 'desired_replicas', 'billing_reference', 'approved_by', 'activated_at')
    list_filter = ('status',)
    readonly_fields = ('environment', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if obj.status == HighAvailabilityOption.Status.ACTIVE:
            if not obj.approved_by_id:
                obj.approved_by = request.user
            if not obj.activated_at:
                from django.utils import timezone
                obj.activated_at = timezone.now()
        obj.full_clean()
        super().save_model(request, obj, form, change)
