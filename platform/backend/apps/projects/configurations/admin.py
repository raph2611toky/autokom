from django.contrib import admin
from apps.projects.configurations.models import DeploymentConfig, ConfigValidationReport, DeploymentTriggerRule


admin.site.register([DeploymentConfig, ConfigValidationReport, DeploymentTriggerRule])
