from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ValidationError
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_project
from apps.projects.secrets.models import SecretReference
from apps.projects.secrets.serializers import SecretSerializer, SecretWriteSerializer, SecretReplaceSerializer
from apps.projects.secrets.services import get_secret, secret_environment, ensure_unique_secret
from apps.projects.git.services import encrypt
from helpers.responses import api_success
from helpers.errors import handle_error
from apps.projects.secrets.swagger.secret_list import schema as secret_list_schema
from apps.projects.secrets.swagger.secret_create import schema as secret_create_schema
from apps.projects.secrets.swagger.secret_replace import schema as secret_replace_schema
from apps.projects.secrets.swagger.secret_delete import schema as secret_delete_schema


class SecretListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @secret_list_schema
    def get(self, request, project_id):
        try:
            project = get_project(request.user, project_id)
            return api_success({'secrets': SecretSerializer(project.secrets.order_by('name'), many=True).data},
                               'Métadonnées des secrets. Valeurs confidentielles masquées.')
        except Exception as exc:
            return handle_error(exc, 'secret_list')


class SecretCreateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @secret_create_schema
    def post(self, request, project_id):
        try:
            project = get_project(request.user, project_id, write=True)
            serializer = SecretWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            attrs = serializer.validated_data
            env = secret_environment(project, attrs)
            ensure_unique_secret(project, env, attrs['name'])
            secret = SecretReference.objects.create(
                project=project, organization=project.organization, environment=env,
                scope=attrs['scope'], name=attrs['name'], encrypted_value=encrypt(attrs['value']),
                version_ref='1',
            )
            return api_success({'secret': SecretSerializer(secret).data}, 'Secret chiffré et enregistré.', 201)
        except Exception as exc:
            return handle_error(exc, 'secret_create')


class SecretReplaceAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @secret_replace_schema
    def patch(self, request, project_id, secret_id):
        try:
            project = get_project(request.user, project_id, write=True)
            secret = get_secret(project, secret_id)
            serializer = SecretReplaceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            secret.encrypted_value = encrypt(serializer.validated_data['value'])
            secret.vault_reference = ''
            secret.version_ref = str(int(secret.version_ref or '0') + 1)
            secret.is_active = True
            secret.save(update_fields=['encrypted_value', 'vault_reference', 'version_ref', 'is_active', 'updated_at'])
            return api_success({'secret': SecretSerializer(secret).data}, 'Valeur du secret remplacée.')
        except Exception as exc:
            return handle_error(exc, 'secret_replace')


class SecretDeleteAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @secret_delete_schema
    def delete(self, request, project_id, secret_id):
        try:
            project = get_project(request.user, project_id, write=True)
            secret = get_secret(project, secret_id)
            # Les variables qui référencent le secret doivent être détachées d'abord.
            if secret.environment_variables.exists():
                raise ValidationError('Secret utilisé par des variables : détacher ces variables avant suppression.')
            secret.delete()
            return api_success(None, 'Secret supprimé.')
        except Exception as exc:
            return handle_error(exc, 'secret_delete')
