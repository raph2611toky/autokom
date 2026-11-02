import uuid
from django.db import models

class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='notifications')
    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, null=True, blank=True)
    kind = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    message = models.TextField()
    target_type = models.CharField(max_length=70, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Notification : {self.title}'

    class Meta:
        db_table = 'notifications_notification'

class NotificationPreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='notification_preferences')
    event_kind = models.CharField(max_length=100)
    channel = models.CharField(max_length=20, default='IN_APP')
    is_enabled = models.BooleanField(default=True)
    class Meta:
        db_table = 'notifications_notificationpreference'
        constraints = [models.UniqueConstraint(fields=['user', 'event_kind', 'channel'], name='unique_notification_preference')]

    def __str__(self):
        return f'NotificationPreference : {self.pk}'

class NotificationDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    notification = models.ForeignKey('notifications.Notification', on_delete=models.CASCADE, related_name='deliveries')
    channel = models.CharField(max_length=30)
    status = models.CharField(max_length=30, default='PENDING')
    attempts = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=100, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'NotificationDelivery : {self.status}'

    class Meta:
        db_table = 'notifications_notificationdelivery'
