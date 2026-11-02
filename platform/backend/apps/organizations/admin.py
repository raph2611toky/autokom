from django.contrib import admin
from apps.organizations.models import Organization, OrganizationMembership, OrganizationInvitation


admin.site.register([Organization, OrganizationMembership, OrganizationInvitation])
