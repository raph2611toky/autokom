from django.contrib import admin
from apps.accounts.user.models import User, EmailVerification, PasswordResetRequest, UserSession, MFADevice, UserPreference


@admin.register(User)
class ClientUserAdmin(admin.ModelAdmin):
    list_display = ('email', 'full_name', 'is_active', 'is_email_verified', 'created_at')
    search_fields = ('email', 'full_name')
    list_filter = ('is_active', 'is_email_verified')
    readonly_fields = ('password', 'token_version', 'last_login_at', 'password_changed_at')
    exclude = ('avatar',)

    def has_add_permission(self, request):
        return False  # Les clients s'inscrivent exclusivement via le service métier.


admin.site.register([EmailVerification, PasswordResetRequest, UserSession, MFADevice, UserPreference])
