from django.contrib import admin
from apps.projects.git.models import GitConnection, GitWebhookEvent

@admin.register(GitConnection)
class GitConnectionAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(GitWebhookEvent)
class GitWebhookEventAdmin(admin.ModelAdmin):
    list_per_page = 30

