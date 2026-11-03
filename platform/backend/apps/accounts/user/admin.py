from django.contrib import admin
from apps.accounts.user.models import User, UserActionToken, UserSession


@admin.register(User)
class ClientUserAdmin(admin.ModelAdmin):
    list_display = ('email', 'full_name', 'is_active', 'is_email_verified', 'created_at')
    search_fields = ('email', 'full_name')
    list_filter = ('is_active', 'is_email_verified')
    readonly_fields = ('password', 'token_version', 'last_login_at', 'password_changed_at')
    exclude = ('avatar',)

    def has_add_permission(self, request):
        return False


@admin.register(UserActionToken)
class UserActionTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'purpose', 'created_at', 'expires_at', 'consumed_at')
    readonly_fields = ('token_hash',)


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'expires_at', 'revoked_at')
    readonly_fields = ('jti_hash',)
