from django.contrib import admin
from apps.projects.configurations.models import DeploymentConfig

@admin.register(DeploymentConfig)
class DeploymentConfigAdmin(admin.ModelAdmin):
    list_per_page = 30

