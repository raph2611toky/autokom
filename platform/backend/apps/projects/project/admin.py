from django.contrib import admin
from apps.projects.project.models import Project, ProjectMember, ProjectTag


admin.site.register([Project, ProjectMember, ProjectTag])
