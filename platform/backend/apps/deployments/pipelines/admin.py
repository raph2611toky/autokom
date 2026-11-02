from django.contrib import admin
from apps.deployments.pipelines.models import PipelineStageRun, PipelineStageEvent, PipelineLogChunk, PipelineExternalExecution, PipelineFinding


admin.site.register([PipelineStageRun, PipelineStageEvent, PipelineLogChunk, PipelineExternalExecution, PipelineFinding])
