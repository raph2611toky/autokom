from django.contrib import admin
from apps.infrastructure.tools.models import IntegrationAdapter, ToolCapability, IntegrationHealthCheck, DeploymentPool


admin.site.register([IntegrationAdapter, ToolCapability, IntegrationHealthCheck, DeploymentPool])
