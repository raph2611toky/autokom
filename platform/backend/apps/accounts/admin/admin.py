from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from apps.accounts.admin.models import Administrator
from apps.accounts.admin.forms import AdministratorCreationForm, AdministratorChangeForm

@admin.register(Administrator)
class AdministratorAdmin(UserAdmin):
    model = Administrator
    add_form = AdministratorCreationForm
    form = AdministratorChangeForm
    ordering = ('email',)
    list_display = ('email', 'is_active', 'is_staff', 'is_superuser')
    search_fields = ('email', 'first_name', 'last_name')
    readonly_fields = ('last_login', 'date_joined')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Informations', {'fields': ('first_name', 'last_name')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Historique', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2')}),
    )

    # Administrateurs créés uniquement via l'assistant local AUTOKOM.
    # Le Django Admin demeure utilisable pour les consultations et modifications
    # permises, mais ne permet pas d'ajouter/supprimer un administrateur.
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
