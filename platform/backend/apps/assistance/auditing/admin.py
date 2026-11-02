from django.contrib import admin
from apps.assistance.auditing.models import AuditEvent


admin.site.register([AuditEvent])
