from django.contrib import admin
from apps.infrastructure.operations.models import AsyncOperation, OperationAttempt, OutboxEvent


admin.site.register([AsyncOperation, OperationAttempt, OutboxEvent])
