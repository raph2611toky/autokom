from django.contrib import admin
from apps.deployments.deployment.models import DeploymentRun, DeploymentApproval, DeploymentTriggerEvent


admin.site.register([DeploymentRun, DeploymentApproval, DeploymentTriggerEvent])
