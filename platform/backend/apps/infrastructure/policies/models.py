import uuid
from django.db import models

class PipelinePolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    name = models.CharField(max_length=170, unique=True)
    description = models.TextField(blank=True)
    scope = models.CharField(max_length=30, default='GLOBAL')
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return f'PipelinePolicy : {self.name}'

    class Meta:
        db_table = 'policies_pipelinepolicy'

class PipelinePolicyVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    policy = models.ForeignKey('policies.PipelinePolicy', on_delete=models.PROTECT, related_name='versions')
    version = models.PositiveIntegerField()
    mode = models.CharField(max_length=12, choices=[('DEVOPS', 'DevOps'), ('DEVSECOPS', 'DevSecOps')])
    rules = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'policies_pipelinepolicyversion'
        constraints = [models.UniqueConstraint(fields=['policy', 'version'], name='unique_pipeline_policy_version')]

    def __str__(self):
        return f'PipelinePolicyVersion : {self.version}'

class PipelineStageRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    policy_version = models.ForeignKey('policies.PipelinePolicyVersion', on_delete=models.PROTECT, related_name='stage_rules')
    key = models.CharField(max_length=140)
    title = models.CharField(max_length=180)
    position = models.PositiveSmallIntegerField()
    capability = models.ForeignKey('infrastructure.ToolCapability', on_delete=models.PROTECT, null=True, blank=True)
    is_required = models.BooleanField(default=False)
    condition = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = 'policies_pipelinestagerule'
        constraints = [models.UniqueConstraint(fields=['policy_version', 'key'], name='unique_policy_stage_key'),
            models.UniqueConstraint(fields=['policy_version', 'position'], name='unique_policy_stage_position')]

    def __str__(self):
        return f'PipelineStageRule : {self.title}'
