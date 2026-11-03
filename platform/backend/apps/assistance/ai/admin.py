from django.contrib import admin
from apps.assistance.ai.models import AIInteraction, AISuggestion

@admin.register(AIInteraction)
class AIInteractionAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(AISuggestion)
class AISuggestionAdmin(admin.ModelAdmin):
    list_per_page = 30

