import uuid
from django.db import models

class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Kind(models.TextChoices):
        STATIC = 'STATIC', 'Site statique'
        WEB = 'WEB', 'Application web'
        API = 'API', 'API'
        WORKER = 'WORKER', 'Worker'
        OTHER = 'OTHER', 'Autre'
    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, related_name='projects')
    repository = models.ForeignKey('integrations.GitRepository', on_delete=models.SET_NULL, null=True, blank=True, related_name='projects')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='created_projects')
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.WEB)
    framework = models.CharField(max_length=90, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'projects_project'
        constraints = [models.UniqueConstraint(fields=['organization', 'slug'], name='unique_project_slug_per_org')]

    def __str__(self):
        return f'Project : {self.name}'

class ProjectMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Role(models.TextChoices):
        MAINTAINER = 'MAINTAINER', 'Mainteneur'
        DEVELOPER = 'DEVELOPER', 'Développeur'
        VIEWER = 'VIEWER', 'Lecteur'
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='project_memberships')
    role = models.CharField(max_length=12, choices=Role.choices)
    class Meta:
        db_table = 'projects_projectmember'
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='unique_project_member')]

    def __str__(self):
        return f'ProjectMember : {self.pk}'

class ProjectTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='tags')
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=16, default='#64748b')
    class Meta:
        db_table = 'projects_projecttag'
        constraints = [models.UniqueConstraint(fields=['project', 'name'], name='unique_project_tag')]

    def __str__(self):
        return f'ProjectTag : {self.name}'
