from django.contrib import admin
from django.utils import timezone
from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption


@admin.register(ApplicationEntitlement)
class ApplicationEntitlementAdmin(admin.ModelAdmin):
    list_display = ('environment', 'status', 'grant_source', 'resource_plan', 'mode_offer', 'approved_by')
    list_filter = ('status', 'grant_source', 'resource_plan')
    list_select_related = ('environment', 'resource_plan', 'mode_offer', 'approved_by')
    list_per_page = 30

    def save_model(self, request, obj, form, change):
        # Changing a plan in Django Admin is a deliberate grant, not a client UI selection.
        # A premium entitlement cannot be issued by the free SYSTEM source.
        if obj.status == ApplicationEntitlement.Status.ACTIVE and obj.grant_source == ApplicationEntitlement.GrantSource.ADMIN:
            if not obj.approved_by_id:
                obj.approved_by = request.user
            if not obj.activated_at:
                obj.activated_at = timezone.now()
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(HighAvailabilityOption)
class HighAvailabilityOptionAdmin(admin.ModelAdmin):
    list_display = ('environment', 'status', 'desired_replicas', 'billing_reference', 'approved_by', 'activated_at')
    list_filter = ('status',)

    def get_readonly_fields(self, request, obj=None):
        # On creation the environment MUST be editable; otherwise an HA option
        # can never be linked to a project through Django Admin.
        return ('created_at', 'updated_at') + (('environment',) if obj else ())

    def save_model(self, request, obj, form, change):
        if obj.status == HighAvailabilityOption.Status.ACTIVE:
            if not obj.approved_by_id:
                obj.approved_by = request.user
            if not obj.activated_at:
                obj.activated_at = timezone.now()
        obj.full_clean()
        super().save_model(request, obj, form, change)
