from django.contrib import admin
from apps.assistance.ai.models import AIProviderConfig, AIInteraction, AISuggestion


admin.site.register([AIProviderConfig, AIInteraction, AISuggestion])
