from django.contrib import admin
from apps.deployments.sites.models import HostedApplication, SiteHealthCheck, SiteAction


admin.site.register([HostedApplication, SiteHealthCheck, SiteAction])
