import uuid
from django.db import models

class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Kind(models.TextChoices):
        PERSONAL = 'PERSONAL', 'Personnel'
        TEAM = 'TEAM', 'Équipe'
    owner = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='owned_organizations')
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.PERSONAL)
    is_active = models.BooleanField(default=True)

    def __str__(self): return self.name

    class Meta:
        db_table = 'organizations_organization'

class OrganizationMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Propriétaire'
        ADMIN = 'ADMIN', 'Administrateur équipe'
        DEVELOPER = 'DEVELOPER', 'Développeur'
        VIEWER = 'VIEWER', 'Lecture seule'
    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'organizations_organizationmembership'
        constraints = [models.UniqueConstraint(fields=['organization', 'user'], name='unique_org_user')]

    def __str__(self):
        return f'OrganizationMembership : {self.pk}'

class OrganizationInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    role = models.CharField(max_length=12, choices=OrganizationMembership.Role.choices)
    invited_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='sent_org_invitations')
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'OrganizationInvitation : {self.email}'

    class Meta:
        db_table = 'organizations_organizationinvitation'
