from django.contrib import admin
from apps.projects.secrets.models import SecretReference


@admin.register(SecretReference)
class SecretReferenceAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'scope', 'environment', 'is_active', 'created_at')
    search_fields = ('name', 'project__name')
    list_per_page = 30
    # Les valeurs restent gérées par l'API dédiée; ne jamais montrer un jeton/ciphertext dans le formulaire.
    exclude = ('encrypted_value', 'vault_reference')
