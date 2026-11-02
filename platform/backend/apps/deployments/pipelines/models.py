import uuid
from django.db import models

class PipelineStageRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.CASCADE, related_name='stages')
    key = models.CharField(max_length=140)
    title = models.CharField(max_length=180)
    position = models.PositiveSmallIntegerField()
    tool_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, default='QUEUED', db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = 'pipelines_pipelinestagerun'
        constraints = [models.UniqueConstraint(fields=['deployment', 'key'], name='unique_stage_key_per_run'),
            models.UniqueConstraint(fields=['deployment', 'position'], name='unique_stage_order_per_run')]
        ordering = ['position']

    def __str__(self):
        return f'PipelineStageRun : {self.title}'

class PipelineStageEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    stage = models.ForeignKey('pipelines.PipelineStageRun', on_delete=models.CASCADE, related_name='events')
    sequence = models.PositiveBigIntegerField()
    source_event_id = models.CharField(max_length=250, blank=True)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField()
    class Meta:
        db_table = 'pipelines_pipelinestageevent'
        constraints = [models.UniqueConstraint(fields=['stage', 'sequence'], name='unique_stage_event_seq')]
        ordering = ['sequence']

    def __str__(self):
        return f'PipelineStageEvent : {self.event_type}'

class PipelineLogChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    stage = models.ForeignKey('pipelines.PipelineStageRun', on_delete=models.CASCADE, related_name='log_chunks')
    first_line = models.PositiveBigIntegerField(default=0)
    last_line = models.PositiveBigIntegerField(default=0)
    cursor = models.CharField(max_length=300, blank=True)
    storage_ref = models.CharField(max_length=700)
    size_bytes = models.PositiveBigIntegerField(default=0)
    redacted = models.BooleanField(default=True)

    def __str__(self):
        return f'PipelineLogChunk : {self.pk}'

    class Meta:
        db_table = 'pipelines_pipelinelogchunk'

class PipelineExternalExecution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    deployment = models.ForeignKey('deployments.DeploymentRun', on_delete=models.CASCADE, related_name='external_executions')
    stage = models.ForeignKey('pipelines.PipelineStageRun', on_delete=models.SET_NULL, null=True, blank=True)
    adapter = models.ForeignKey('infrastructure.IntegrationAdapter', on_delete=models.PROTECT, related_name='external_executions')
    external_run_id = models.CharField(max_length=250)
    external_url = models.URLField(max_length=700, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default='PENDING')
    class Meta:
        db_table = 'pipelines_pipelineexternalexecution'
        constraints = [models.UniqueConstraint(fields=['adapter', 'external_run_id'], name='unique_pipeline_external_exec')]

    def __str__(self):
        return f'PipelineExternalExecution : {self.pk}'

class PipelineFinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    stage = models.ForeignKey('pipelines.PipelineStageRun', on_delete=models.CASCADE, related_name='findings')
    scanner = models.CharField(max_length=100)
    rule_id = models.CharField(max_length=180)
    severity = models.CharField(max_length=20)
    source_path = models.CharField(max_length=600, blank=True)
    source_line = models.PositiveIntegerField(null=True, blank=True)
    finding_digest = models.CharField(max_length=64, db_index=True)
    report_ref = models.CharField(max_length=700, blank=True)
    status = models.CharField(max_length=20, default='OPEN')

    def __str__(self):
        return f'PipelineFinding : {self.status}'

    class Meta:
        db_table = 'pipelines_pipelinefinding'
