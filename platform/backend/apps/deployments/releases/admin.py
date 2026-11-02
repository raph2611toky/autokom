from django.contrib import admin
from apps.deployments.releases.models import Release

@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_per_page = 30

