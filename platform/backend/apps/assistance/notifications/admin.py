from django.contrib import admin
from apps.assistance.notifications.models import Notification, NotificationPreference, NotificationDelivery


admin.site.register([Notification, NotificationPreference, NotificationDelivery])
