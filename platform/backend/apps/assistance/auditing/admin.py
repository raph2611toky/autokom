from django.contrib import admin
from apps.assistance.auditing.models import AuditEvent

@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_per_page = 30

