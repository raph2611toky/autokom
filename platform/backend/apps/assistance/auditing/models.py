import uuid

from django.db import models


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_actor = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_events')
    admin_actor = models.ForeignKey('administrators.Administrator', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_events')
    organization = models.ForeignKey('organizations.Organization', on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=140)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100, blank=True)
    success = models.BooleanField(default=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    correlation_id = models.CharField(max_length=120, blank=True)
    metadata = models.JSONField(default=dict, blank=True, help_text='Aucune donnée sensible.')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f'{self.action} — {self.pk}'

    class Meta:
        db_table = 'auditing_auditevent'
