from django.contrib import admin
from apps.assistance.notifications.models import Notification

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_per_page = 30

