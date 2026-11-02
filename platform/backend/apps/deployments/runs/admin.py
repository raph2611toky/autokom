from django.contrib import admin
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, PipelineFinding

@admin.register(DeploymentRun)
class DeploymentRunAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(PipelineStageRun)
class PipelineStageRunAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(PipelineFinding)
class PipelineFindingAdmin(admin.ModelAdmin):
    list_per_page = 30

