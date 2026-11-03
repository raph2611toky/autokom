from django.contrib import admin
from apps.projects.git.models import GitConnection, GitWebhookEvent, GitHubAuthorization, GitHubOAuthState


@admin.register(GitConnection)
class GitConnectionAdmin(admin.ModelAdmin):
    list_display = ('account_name', 'organization', 'installation_id', 'is_active', 'updated_at')
    search_fields = ('account_name', 'external_account_id')
    list_per_page = 30


@admin.register(GitWebhookEvent)
class GitWebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'repository_full_name', 'status', 'received_at')
    search_fields = ('repository_full_name', 'external_event_id')
    list_per_page = 30


@admin.register(GitHubAuthorization)
class GitHubAuthorizationAdmin(admin.ModelAdmin):
    list_display = ('github_login', 'user', 'is_active', 'access_expires_at', 'created_at')
    search_fields = ('github_login', 'user__email')
    readonly_fields = ('user', 'github_user_id', 'github_login', 'is_active',
                       'access_expires_at', 'refresh_expires_at', 'created_at', 'updated_at')
    fields = readonly_fields
    list_per_page = 30

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GitHubOAuthState)
class GitHubOAuthStateAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'expires_at', 'consumed_at')
    readonly_fields = ('user', 'state_hash', 'created_at', 'expires_at', 'consumed_at')
    fields = readonly_fields
    list_per_page = 30

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
