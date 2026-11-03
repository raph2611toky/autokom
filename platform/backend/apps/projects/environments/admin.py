from django.contrib import admin
from apps.projects.environments.models import Environment, EnvironmentVariable

@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(EnvironmentVariable)
class EnvironmentVariableAdmin(admin.ModelAdmin):
    list_per_page = 30

