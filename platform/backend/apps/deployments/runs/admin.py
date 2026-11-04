from django.contrib import admin
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, PipelineFinding, DeploymentLogEntry

@admin.register(DeploymentRun)
class DeploymentRunAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(PipelineStageRun)
class PipelineStageRunAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(PipelineFinding)
class PipelineFindingAdmin(admin.ModelAdmin):
    list_per_page = 30



@admin.register(DeploymentLogEntry)
class DeploymentLogEntryAdmin(admin.ModelAdmin):
    list_display = ('deployment', 'sequence', 'level', 'created_at')
    readonly_fields = ('deployment','sequence','level','message','created_at')
    def has_add_permission(self, request):
        return False
