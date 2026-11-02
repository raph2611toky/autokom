from django.contrib import admin
from apps.infrastructure.policies.models import PipelinePolicy, PipelinePolicyVersion, PipelineStageRule


admin.site.register([PipelinePolicy, PipelinePolicyVersion, PipelineStageRule])
