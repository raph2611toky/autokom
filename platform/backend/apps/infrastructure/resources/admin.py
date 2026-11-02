from django.contrib import admin
from apps.infrastructure.resources.models import ResourcePlan, QuotaAssignment, ResourceAllocation, ResourceReservation, ResourceUsageRollup


admin.site.register([ResourcePlan, QuotaAssignment, ResourceAllocation, ResourceReservation, ResourceUsageRollup])
