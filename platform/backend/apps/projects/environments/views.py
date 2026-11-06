from django.db import transaction
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ValidationError, NotFound
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_project, get_environment
from apps.projects.environments.models import Environment, EnvironmentVariable
from apps.projects.environments.serializers import (EnvironmentSerializer, EnvironmentCreateSerializer,
    EnvironmentUpdateSerializer, EnvironmentVariableSerializer, EnvironmentVariableWriteSerializer,
    DomainSerializer, DomainCreateSerializer, DomainReassignSerializer)
from apps.projects.environments.services import create_environment, get_variable_or_404, validate_variable
from apps.projects.project.services import reserve_domain
from apps.deployments.domains.services import claim_domain, domain_summary
from apps.deployments.domains.models import Domain
from apps.projects.common import validate_hostname
from helpers.responses import api_success
from helpers.errors import handle_error
from apps.projects.environments.swagger.environment_list import schema as environment_list_schema
from apps.projects.environments.swagger.environment_create import schema as environment_create_schema
from apps.projects.environments.swagger.environment_detail import schema as environment_detail_schema
from apps.projects.environments.swagger.environment_update import schema as environment_update_schema
from apps.projects.environments.swagger.variables_list import schema as variables_list_schema
from apps.projects.environments.swagger.variables_create import schema as variables_create_schema
from apps.projects.environments.swagger.variable_update import schema as variable_update_schema
from apps.projects.environments.swagger.variable_delete import schema as variable_delete_schema
from apps.projects.environments.swagger.domains_list import schema as domains_list_schema
from apps.projects.environments.swagger.domains_create import schema as domains_create_schema
from apps.projects.environments.swagger.domain_reassign import schema as domain_reassign_schema


class EnvironmentListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @environment_list_schema
    def get(self, request, project_id):
        try:
            project = get_project(request.user, project_id)
            return api_success({'environments': EnvironmentSerializer(project.environments.order_by('created_at'), many=True).data},
                               'Environnements disponibles.')
        except Exception as exc:
            return handle_error(exc, 'environment_list')


class EnvironmentCreateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @environment_create_schema
    def post(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            serializer = EnvironmentCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            environment = create_environment(project, request.user, serializer.validated_data)
            return api_success({'environment': EnvironmentSerializer(environment).data}, 'Environnement créé.', 201)
        except Exception as exc:
            return handle_error(exc, 'environment_create')


class EnvironmentDetailAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @environment_detail_schema
    def get(self, request, project_id, environment_id):
        try:
            _, environment = get_environment(request.user, project_id, environment_id)
            return api_success({'environment': EnvironmentSerializer(environment).data}, 'Environnement consulté.')
        except Exception as exc:
            return handle_error(exc, 'environment_detail')


class EnvironmentUpdateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @environment_update_schema
    def patch(self, request, project_id, environment_id):
        try:
            _, environment = get_environment(request.user, project_id, environment_id, write=True)
            serializer = EnvironmentUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if not serializer.validated_data:
                raise ValidationError('Aucune modification fournie.')
            for k,v in serializer.validated_data.items():
                setattr(environment, k, v)
            environment.save(update_fields=[*serializer.validated_data, 'updated_at'])
            return api_success({'environment': EnvironmentSerializer(environment).data}, 'Environnement modifié.')
        except Exception as exc:
            return handle_error(exc, 'environment_update')


class EnvironmentVariablesListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @variables_list_schema
    def get(self, request, project_id, environment_id):
        try:
            _, environment = get_environment(request.user, project_id, environment_id)
            return api_success({'variables': EnvironmentVariableSerializer(environment.variables.all().order_by('key'), many=True).data},
                               'Variables consultées.')
        except Exception as exc:
            return handle_error(exc, 'environment_variables_list')


class EnvironmentVariableCreateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @variables_create_schema
    def post(self, request, project_id, environment_id):
        try:
            _, environment = get_environment(request.user, project_id, environment_id, write=True)
            serializer = EnvironmentVariableWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            secret = validate_variable(data, environment)
            variable = EnvironmentVariable.objects.create(environment=environment, key=data['key'],
                value=data.get('value', '') or '', secret=secret)
            return api_success({'variable': EnvironmentVariableSerializer(variable).data}, 'Variable créée.', 201)
        except Exception as exc:
            return handle_error(exc, 'environment_variable_create')


class EnvironmentVariableUpdateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @variable_update_schema
    def patch(self, request, project_id, environment_id, variable_id):
        try:
            _, environment = get_environment(request.user, project_id, environment_id, write=True)
            variable = get_variable_or_404(environment, variable_id)
            payload = {'key': variable.key, 'value': variable.value,
                       'secret_id': variable.secret_id, **request.data}
            serializer = EnvironmentVariableWriteSerializer(data=payload)
            serializer.is_valid(raise_exception=True)
            secret = validate_variable(serializer.validated_data, environment, exclude=variable)
            variable.key = serializer.validated_data['key']
            variable.value = serializer.validated_data.get('value', '') or ''
            variable.secret = secret
            variable.full_clean()
            variable.save()
            return api_success({'variable': EnvironmentVariableSerializer(variable).data}, 'Variable modifiée.')
        except Exception as exc:
            return handle_error(exc, 'environment_variable_update')


class EnvironmentVariableDeleteAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @variable_delete_schema
    def delete(self, request, project_id, environment_id, variable_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id, write=True)
            variable = get_variable_or_404(env, variable_id)
            variable.delete()
            return api_success(None, 'Variable supprimée.')
        except Exception as exc:
            return handle_error(exc, 'environment_variable_delete')


class EnvironmentDomainsAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @domains_list_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            return api_success({'domains': [domain_summary(d, include_challenge=True) for d in env.domains.order_by('hostname')]}, 'Domaines de l’environnement.')
        except Exception as exc:
            return handle_error(exc, 'domain_list')


class EnvironmentDomainCreateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @domains_create_schema
    def post(self, request, project_id, environment_id):
        try:
            project, env = get_environment(request.user, project_id, environment_id, write=True)
            serializer = DomainCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            hostname=serializer.validated_data.get('hostname', '').strip()
            if not hostname:
                raise ValidationError({'hostname':'Nom du domaine requis.'})
            from apps.deployments.domains.services import _managed
            kind=Domain.Type.SUBDOMAIN if _managed(hostname.lower().strip('.')) else Domain.Type.CUSTOM
            domain = claim_domain(request.user, project_id, environment_id, hostname, kind)
            return api_success({'domain': domain_summary(domain, include_challenge=True)},
                               'Domaine réservé. Activez la vérification DNS/HTTPS.', 201)
        except Exception as exc:
            return handle_error(exc, 'domain_create')


class DomainReassignAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @domain_reassign_schema
    def patch(self, request, project_id, environment_id, domain_id):
        try:
            project, env = get_environment(request.user, project_id, environment_id, write=True)
            serializer = DomainReassignSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            domain = Domain.objects.filter(pk=domain_id, organization=project.organization, environment=env).first()
            if domain is None:
                raise NotFound('Domaine introuvable.')
            new_env = Environment.objects.filter(pk=serializer.validated_data['new_environment_id'],
                                                  project=project).first()
            if new_env is None:
                raise ValidationError({'new_environment_id': 'Environnement cible invalide.'})
            domain.environment = new_env
            domain.status = Domain.Status.PENDING
            domain.tls_status = ''
            domain.save(update_fields=['environment', 'status', 'tls_status', 'updated_at'])
            return api_success({'domain': DomainSerializer(domain).data}, 'Domaine réaffecté, vérification requise.')
        except Exception as exc:
            return handle_error(exc, 'domain_reassign')
