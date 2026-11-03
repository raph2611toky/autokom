from rest_framework.permissions import BasePermission
from apps.accounts.user.models import User

class IsClientUser(BasePermission):
    message = 'Authentification client requise.'
    def has_permission(self, request, view):
        user = request.user
        return isinstance(user, User) and user.is_active and user.is_email_verified

class HasOrganizationRole(BasePermission):
    """Reusable object role helper; views must implement get_organization()."""
    allowed_roles = ('OWNER', 'ADMIN', 'DEVELOPER', 'VIEWER')
    def has_permission(self, request, view):
        if not IsClientUser().has_permission(request, view):
            return False
        getter = getattr(view, 'get_organization', None)
        if not callable(getter):
            return False
        organization = getter()
        return organization.memberships.filter(user=request.user, is_active=True, role__in=self.allowed_roles).exists()

class CanManageOrganization(HasOrganizationRole):
    allowed_roles = ('OWNER', 'ADMIN')

class CanDeployProject(BasePermission):
    """Collaborator-level permission, to be used in future project views."""
    def has_object_permission(self, request, view, project):
        if not IsClientUser().has_permission(request, view):
            return False
        if project.organization.memberships.filter(
            user=request.user, is_active=True, role__in=('OWNER', 'ADMIN', 'DEVELOPER')
        ).exists():
            return True
        return project.members.filter(user=request.user, role__in=('MAINTAINER', 'DEVELOPER')).exists()
