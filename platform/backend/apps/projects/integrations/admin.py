from django.contrib import admin
from apps.projects.integrations.models import GitConnection, GitRepository, WebhookEndpoint, WebhookDelivery


admin.site.register([GitConnection, GitRepository, WebhookEndpoint, WebhookDelivery])
