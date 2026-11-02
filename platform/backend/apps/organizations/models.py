import uuid

from django.db import models


class Organization(models.Model):
    class Kind(models.TextChoices):
        PERSONAL = 'PERSONAL', 'Personnel'
        TEAM = 'TEAM', 'Équipe'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='owned_organizations')
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.PERSONAL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'organizations_organization'


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Propriétaire'
        ADMIN = 'ADMIN', 'Administrateur équipe'
        DEVELOPER = 'DEVELOPER', 'Développeur'
        VIEWER = 'VIEWER', 'Lecteur'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user.email} / {self.organization.name} ({self.role})'

    class Meta:
        db_table = 'organizations_organizationmembership'
        constraints = [models.UniqueConstraint(fields=['organization', 'user'], name='unique_org_user')]
