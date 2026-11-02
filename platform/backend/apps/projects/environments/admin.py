from django.contrib import admin
from apps.projects.environments.models import Environment, EnvironmentVariable, EnvironmentBranchRule


admin.site.register([Environment, EnvironmentVariable, EnvironmentBranchRule])
