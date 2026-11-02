import uuid

from django.db import models


class Notification(models.Model):
    class Kind(models.TextChoices):
        DEPLOYMENT = 'DEPLOYMENT', 'Déploiement'
        DOMAIN = 'DOMAIN', 'Domaine'
        QUOTA = 'QUOTA', 'Quota'
        ACCOUNT = 'ACCOUNT', 'Compte'
        SECURITY = 'SECURITY', 'Sécurité'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=220)
    message = models.TextField()
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.user.email} — {self.title}'

    class Meta:
        db_table = 'notifications_notification'
        indexes = [models.Index(fields=['user', 'is_read', 'created_at'])]
