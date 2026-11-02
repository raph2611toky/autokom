import uuid

from django.db import models


class Project(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Actif'
        ARCHIVED = 'ARCHIVED', 'Archivé'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, related_name='projects')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='created_projects')
    git_connection = models.ForeignKey('integrations.GitConnection', on_delete=models.SET_NULL, null=True, blank=True, related_name='projects')
    repository_external_id = models.CharField(max_length=160, blank=True)
    repository_name = models.CharField(max_length=300, blank=True)
    repository_url = models.URLField(max_length=500, blank=True)
    default_branch = models.CharField(max_length=255, default='main')
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    framework = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.organization.slug}/{self.slug}'

    class Meta:
        db_table = 'projects_project'
        constraints = [models.UniqueConstraint(fields=['organization', 'slug'], name='unique_project_slug_per_org')]


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        MAINTAINER = 'MAINTAINER', 'Responsable'
        DEVELOPER = 'DEVELOPER', 'Développeur'
        VIEWER = 'VIEWER', 'Lecteur'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='project_memberships')
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.project.slug} — {self.user.email} ({self.role})'

    class Meta:
        db_table = 'projects_projectmember'
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='unique_project_member')]
