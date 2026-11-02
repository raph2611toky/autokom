import uuid
from django.db import models

class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    client_actor = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_events')
    admin_actor = models.ForeignKey('administrators.Administrator', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_events')
    organization = models.ForeignKey('organizations.Organization', on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=160)
    target_type = models.CharField(max_length=100)
    target_id = models.UUIDField(null=True, blank=True)
    success = models.BooleanField(default=True)
    correlation_id = models.CharField(max_length=150, blank=True)
    metadata = models.JSONField(default=dict)
    occurred_at = models.DateTimeField()

    def __str__(self):
        return f'AuditEvent : {self.action}'

    class Meta:
        db_table = 'auditing_auditevent'
