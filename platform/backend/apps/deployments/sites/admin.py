from django.contrib import admin
from apps.deployments.sites.models import HostedApplication

@admin.register(HostedApplication)
class HostedApplicationAdmin(admin.ModelAdmin):
    list_per_page = 30

