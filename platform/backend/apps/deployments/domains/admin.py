from django.contrib import admin
from apps.deployments.domains.models import Domain

@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_per_page = 30

