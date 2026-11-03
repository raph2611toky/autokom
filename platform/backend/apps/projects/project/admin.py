from django.contrib import admin
from apps.projects.project.models import Project, ProjectMember

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_per_page = 30

@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_per_page = 30

