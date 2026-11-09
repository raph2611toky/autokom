from django.db import transaction
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from apps.accounts.user.permissions import IsClientUser
from apps.organizations.models import OrganizationMembership
from apps.projects.common import organizations_for_user, projects_for_user, get_project, int_param
from apps.projects.project.models import Project, ProjectMember
from apps.accounts.user.models import User
from apps.organizations.models import OrganizationMembership
from apps.projects.project.serializers import (ProjectSerializer, ProjectCreateSerializer,
    ProjectPatchSerializer, ProjectMemberSerializer, ProjectMemberCreateSerializer, ProjectMemberUpdateSerializer)
from apps.projects.project.services import create_project
from helpers.responses import api_success
from apps.projects.project.swagger.members_list import schema as members_list_schema
from apps.projects.project.swagger.members_add import schema as members_add_schema
from apps.projects.project.swagger.members_update import schema as members_update_schema
from apps.projects.project.swagger.members_remove import schema as members_remove_schema
from helpers.errors import handle_error
from apps.projects.project.swagger.organizations import schema as organizations_schema
from apps.projects.project.swagger.project_list import schema as list_schema
from apps.projects.project.swagger.project_create import schema as create_schema
from apps.projects.project.swagger.project_detail import schema as detail_schema
from apps.projects.project.swagger.project_update import schema as update_schema
from apps.projects.project.swagger.project_archive import schema as archive_schema


class ProjectOrganizationsAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @organizations_schema
    def get(self, request):
        try:
            result = []
            for org in organizations_for_user(request.user).order_by('name'):
                membership = OrganizationMembership.objects.filter(organization=org, user=request.user, is_active=True).first()
                result.append({'id': str(org.id), 'name': org.name, 'slug': org.slug,
                               'role': membership.role if membership else 'OWNER'})
            return api_success({'organizations': result}, 'Vos espaces AUTOKOM.')
        except Exception as exc:
            return handle_error(exc, 'project_organizations')


class ProjectListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @list_schema
    def get(self, request):
        try:
            qs = projects_for_user(request.user).select_related('organization', 'git_connection').order_by('-created_at')
            if request.query_params.get('organization_id'):
                from apps.projects.common import get_organization
                org = get_organization(request.user, request.query_params['organization_id'])
                qs = qs.filter(organization=org)
            page = int_param(request, 'page', default=1, maximum=10000)
            size = int_param(request, 'page_size', default=20, maximum=100)
            count = qs.count()
            return api_success({'projects': ProjectSerializer(qs[(page-1)*size:page*size], many=True).data,
                                'page': page, 'page_size': size, 'total': count}, 'Vos projets AUTOKOM.')
        except Exception as exc:
            return handle_error(exc, 'project_list')


class ProjectCreateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @create_schema
    def post(self, request):
        try:
            serializer = ProjectCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            project = create_project(request.user, serializer.validated_data)
            environment = project.environments.get(slug='production')
            domain = environment.domains.first()
            return api_success({'project': ProjectSerializer(project).data,
                                'environment_id': str(environment.id),
                                'domain': domain.hostname if domain else None}, 'Projet créé et configuration initiale préparée.', 201)
        except Exception as exc:
            return handle_error(exc, 'project_create')


class ProjectDetailAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @detail_schema
    def get(self, request, project_id):
        try:
            project = get_project(request.user, project_id)
            environments = [{'id': str(e.id), 'name': e.name, 'slug': e.slug, 'kind': e.kind}
                            for e in project.environments.all().order_by('created_at')]
            return api_success({'project': ProjectSerializer(project).data, 'environments': environments}, 'Projet consulté.')
        except Exception as exc:
            return handle_error(exc, 'project_detail')


class ProjectUpdateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @update_schema
    def patch(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            serializer = ProjectPatchSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            for key, value in serializer.validated_data.items():
                setattr(project, key, value)
            project.save(update_fields=[*serializer.validated_data, 'updated_at'])
            return api_success({'project': ProjectSerializer(project).data}, 'Projet modifié.')
        except Exception as exc:
            return handle_error(exc, 'project_update')


class ProjectArchiveAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @archive_schema
    def post(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            project.status = Project.Status.ARCHIVED
            project.save(update_fields=['status', 'updated_at'])
            return api_success({'project': ProjectSerializer(project).data}, 'Projet archivé : les nouvelles publications sont désactivées.')
        except Exception as exc:
            return handle_error(exc, 'project_archive')



class ProjectRestoreAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    def post(self, request, project_id):
        try:
            project=get_project(request.user,project_id,write=True)
            if project.status!=Project.Status.ARCHIVED:
                raise ValidationError({'project':'Seuls les projets archivés peuvent être restaurés.'})
            project.status=Project.Status.ACTIVE
            project.save(update_fields=['status','updated_at'])
            return api_success({'project':ProjectSerializer(project).data},'Projet restauré. Nouvelle publication autorisée.')
        except Exception as exc:
            return handle_error(exc,'project_restore')


class ProjectDeleteAPIView(APIView):
    """Safely take a project offline and hide it; preserve immutable release and audit history.

    We deliberately do not cascade-delete PROTECT-related releases, accounts or domain
    ownership proofs. Deprovisioning is retriable if the broker becomes unavailable.
    """
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]

    def post(self, request, project_id):
        try:
            from apps.deployments.runs.autokom_socket import request_control
            from apps.deployments.runs.models import DeploymentRun
            project=get_project(request.user,project_id,write=True)
            if not (project.organization.owner_id == request.user.id or
                    OrganizationMembership.objects.filter(organization=project.organization,user=request.user,
                        role__in=['OWNER','ADMIN'],is_active=True).exists()):
                raise PermissionDenied('Suppression réservée au propriétaire ou administrateur de l’organisation.')
            if request.data.get('confirm_name') != project.name:
                raise ValidationError({'confirm_name':'Saisissez le nom exact du projet pour confirmer.'})
            environments=list(project.environments.all())
            from apps.deployments.runs.models import StackDeployment
            if (DeploymentRun.objects.filter(environment__in=environments,status__in=['QUEUED','RUNNING','PREPARED']).exists() or
                StackDeployment.objects.filter(stack__project=project, status__in=['QUEUED','PREPARING','ACTIVATING','BLOCKED']).exists()):
                raise ValidationError({'project':'Publication en cours : attendre sa fin avant la suppression.'})
            for env in environments:
                request_control('runtime.deprovision', {
                    'environment_id':str(env.id),
                    'hostnames':list(env.domains.values_list('hostname',flat=True)),
                },timeout=65)
            project.status=Project.Status.DELETED
            project.save(update_fields=['status','updated_at'])
            return api_success({'deleted':True,'history_retained':True},
                'Projet retiré, publications arrêtées et domaines désactivés. Historique conservé pour audit.')
        except Exception as exc:
            return handle_error(exc,'project_delete')


def can_manage_members(project, user):
    if project.organization.owner_id == user.id:
        return True
    if OrganizationMembership.objects.filter(organization=project.organization, user=user,
            role__in=['OWNER','ADMIN'], is_active=True).exists():
        return True
    return project.members.filter(user=user, role='MAINTAINER').exists()


class ProjectMembersListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @members_list_schema
    def get(self, request, project_id):
        try:
            project = get_project(request.user, project_id)
            members = ProjectMember.objects.filter(project=project).select_related('user').order_by('user__email')
            return api_success({'members': [
                {'id': str(m.id), 'email': m.user.email, 'role': m.role} for m in members
            ]}, 'Collaborateurs du projet.')
        except Exception as exc:
            return handle_error(exc, 'project_members_list')


class ProjectMemberAddAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @members_add_schema
    def post(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            if not can_manage_members(project, request.user):
                raise PermissionDenied('Gestion des collaborateurs réservée aux responsables.')
            serializer = ProjectMemberCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            member = User.objects.filter(email__iexact=data['email'], is_active=True, is_email_verified=True).first()
            if member is None or not OrganizationMembership.objects.filter(
                organization=project.organization, user=member, is_active=True).exists():
                raise ValidationError({'email': 'Utilisateur introuvable parmi les membres de l’organisation.'})
            if ProjectMember.objects.filter(project=project, user=member).exists():
                raise ValidationError({'email': 'Utilisateur déjà membre de ce projet.'})
            membership = ProjectMember.objects.create(project=project, user=member, role=data['role'])
            return api_success({'member': {'id': str(membership.id), 'email': member.email, 'role': membership.role}},
                               'Collaborateur ajouté.', 201)
        except Exception as exc:
            return handle_error(exc, 'project_members_add')


class ProjectMemberUpdateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @members_update_schema
    def patch(self, request, project_id, membership_id):
        try:
            project = get_project(request.user, project_id, write=True)
            if not can_manage_members(project, request.user):
                raise PermissionDenied('Gestion des collaborateurs réservée aux responsables.')
            membership = ProjectMember.objects.select_related('user').filter(project=project, pk=membership_id).first()
            if membership is None:
                raise NotFound('Membre de projet introuvable.')
            serializer = ProjectMemberUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            membership.role = serializer.validated_data['role']
            membership.save(update_fields=['role'])
            return api_success({'member': {'id': str(membership.id), 'email': membership.user.email,
                                            'role': membership.role}}, 'Rôle du collaborateur modifié.')
        except Exception as exc:
            return handle_error(exc, 'project_members_update')


class ProjectMemberRemoveAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @members_remove_schema
    def delete(self, request, project_id, membership_id):
        try:
            project = get_project(request.user, project_id, write=True)
            if not can_manage_members(project, request.user):
                raise PermissionDenied('Gestion des collaborateurs réservée aux responsables.')
            membership = ProjectMember.objects.filter(project=project, pk=membership_id).first()
            if membership is None:
                raise NotFound('Membre de projet introuvable.')
            membership.delete()
            return api_success(None, 'Collaborateur retiré du projet.')
        except Exception as exc:
            return handle_error(exc, 'project_members_remove')
