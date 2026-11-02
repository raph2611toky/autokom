from django.contrib import admin
from apps.deployments.releases.models import BuildArtifact, Release


admin.site.register([BuildArtifact, Release])
