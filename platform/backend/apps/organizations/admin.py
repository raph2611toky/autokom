from django.contrib import admin
from apps.organizations.models import Organization, OrganizationMembership

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_per_page = 30

