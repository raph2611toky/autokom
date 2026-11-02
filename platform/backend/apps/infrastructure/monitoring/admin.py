from django.contrib import admin
from apps.infrastructure.monitoring.models import MonitoringRule, MonitoringAlert, HealthSnapshot


admin.site.register([MonitoringRule, MonitoringAlert, HealthSnapshot])
