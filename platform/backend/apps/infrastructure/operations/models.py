import uuid
from django.db import models

class AsyncOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    requested_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    operation_type = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=20, default='QUEUED', db_index=True)
    idempotency_key = models.CharField(max_length=150, unique=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f'AsyncOperation : {self.status}'

    class Meta:
        db_table = 'operations_asyncoperation'

class OperationAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    operation = models.ForeignKey('operations.AsyncOperation', on_delete=models.CASCADE, related_name='attempts')
    number = models.PositiveSmallIntegerField()
    worker_identifier = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=100, blank=True)
    class Meta:
        db_table = 'operations_operationattempt'
        constraints = [models.UniqueConstraint(fields=['operation', 'number'], name='unique_operation_attempt_number')]

    def __str__(self):
        return f'OperationAttempt : {self.number}'

class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    event_id = models.UUIDField(unique=True)
    topic = models.CharField(max_length=160)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='QUEUED')
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'OutboxEvent : {self.status}'

    class Meta:
        db_table = 'operations_outboxevent'
