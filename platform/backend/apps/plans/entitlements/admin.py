from django.contrib import admin
from django.db import transaction
from django.utils import timezone
from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption, SandboxCheckout


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


@admin.register(SandboxCheckout)
class SandboxCheckoutAdmin(admin.ModelAdmin):
    list_display = ('reference', 'environment', 'created_by', 'plan_code', 'status', 'amount_ariary', 'expires_at')
    list_filter = ('status', 'plan_code', 'period')
    search_fields = ('reference', 'environment__project__name', 'created_by__email')
    actions = ('revoke_sandbox_receipts',)
    readonly_fields = ('id', 'reference', 'environment', 'created_by', 'resource_plan',
                       'plan_code', 'period', 'amount_ariary', 'idempotency_key', 'status', 'expires_at', 'created_at')
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Les reçus sont immuables : utiliser l’action de révocation.')
    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Révoquer les simulations sélectionnées (et leurs droits TEST)')
    def revoke_sandbox_receipts(self, request, queryset):
        with transaction.atomic():
            for receipt in queryset.select_for_update():
                if receipt.status == SandboxCheckout.Status.REVOKED:
                    continue
                receipt.status = SandboxCheckout.Status.REVOKED
                receipt.save(update_fields=['status'])
                ApplicationEntitlement.objects.filter(
                    sandbox_checkout=receipt, grant_source=ApplicationEntitlement.GrantSource.TEST,
                ).update(status=ApplicationEntitlement.Status.REVOKED)
                HighAvailabilityOption.objects.filter(
                    sandbox_checkout=receipt,
                ).update(status=HighAvailabilityOption.Status.REJECTED)
