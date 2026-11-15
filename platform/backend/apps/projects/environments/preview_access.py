"""Authorized, environment-scoped visibility and one-time credential delivery."""
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.views import APIView

from apps.accounts.user.permissions import IsClientUser
from apps.deployments.domains.models import Domain
from apps.deployments.domains.services import _reject_domain_mutation_during_deployment
from apps.deployments.runs.autokom_socket import request_control
from apps.projects.common import get_environment
from apps.projects.configurations.models import ServiceStack
from apps.projects.environments.models import Environment
from apps.projects.project.models import Project
from helpers.errors import handle_error
from helpers.responses import api_success


class PreviewVisibilitySerializer(serializers.Serializer):
    private = serializers.BooleanField()


def eligible(env):
    return env.kind == Environment.Kind.STAGING or (
        getattr(settings, 'AUTOKOM_PLATFORM_PREPROD', False)
        and env.kind == Environment.Kind.PRODUCTION and env.slug == 'production')


def authorized(user, project_id, environment_id, *, mutate=False):
    project, env = get_environment(user, project_id, environment_id, write=True)
    if mutate:
        project = Project.objects.select_for_update().get(pk=project.pk)
        env = Environment.objects.select_for_update().get(pk=env.pk, project=project)
    if project.status != Project.Status.ACTIVE or not env.is_active or not eligible(env):
        raise NotFound('Préproduction active introuvable.')
    if ServiceStack.objects.filter(project=project).exists():
        raise ValidationError({'preview': 'Le mode WireGuard est disponible pour les projets à un service.'})
    return env


def payload(env):
    domains = list(env.domains.order_by('-is_primary', 'hostname'))
    if not domains or any(d.domain_type != Domain.Type.SUBDOMAIN for d in domains):
        raise ValidationError({'preview': 'Seuls les sous-domaines AUTOKOM sont disponibles en mode privé.'})
    return {'environment_id': str(env.pk), 'kind': env.kind,
            'hostnames': [d.hostname for d in domains]}, domains


def presentation(env, domains, *, issued=None):
    active = next((d for d in domains if not d.is_disabled and d.status == Domain.Status.ACTIVE
                   and d.tls_status == 'ACTIVE'), None)
    return {'private': env.wireguard_only, 'issued': issued,
            'ready': bool(active), 'url': 'https://' + active.hostname + '/' if active else None}


class PreviewAccessAPIView(APIView):
    permission_classes = [IsClientUser]
    parser_classes = [JSONParser]

    def get(self, request, project_id, environment_id):
        try:
            env = authorized(request.user, project_id, environment_id)
            args, domains = payload(env)
            host = request_control('preview.status', args, timeout=10)
            # Do not show 'private' as applied when the host and DB disagree.
            if host.get('private') is not env.wireguard_only:
                raise ValidationError({'preview': 'État de visibilité non confirmé. Enregistrez de nouveau la visibilité.'})
            response = api_success(presentation(env, domains, issued=host.get('issued')), 'Visibilité consultée.')
            response['Cache-Control'] = 'no-store'
            return response
        except Exception as exc:
            return handle_error(exc, 'preview_status')

    def patch(self, request, project_id, environment_id):
        try:
            form = PreviewVisibilitySerializer(data=request.data)
            form.is_valid(raise_exception=True)
            private = form.validated_data['private']
            with transaction.atomic():
                env = authorized(request.user, project_id, environment_id, mutate=True)
                _reject_domain_mutation_during_deployment(env)
                args, domains = payload(env)
                result = request_control('preview.visibility', {**args, 'private': private}, timeout=68)
                if not isinstance(result, dict) or result.get('private') is not private:
                    raise ValidationError({'preview': 'Changement de visibilité non confirmé.'})
                env.wireguard_only = private
                env.save(update_fields=['wireguard_only', 'updated_at'])
            return api_success(presentation(env, domains), 'Visibilité enregistrée.')
        except Exception as exc:
            return handle_error(exc, 'preview_visibility')


class PreviewCredentialsAPIView(APIView):
    permission_classes = [IsClientUser]
    parser_classes = [JSONParser]

    def post(self, request, project_id, environment_id):
        try:
            with transaction.atomic():
                env = authorized(request.user, project_id, environment_id, mutate=True)
                args, domains = payload(env)
                if not env.wireguard_only or not presentation(env, domains)['ready']:
                    raise ValidationError({'preview': 'Une publication privée active est nécessaire.'})
                _reject_domain_mutation_during_deployment(env)
                result = request_control('preview.credentials', {**args, 'operation': 'issue'}, timeout=35)
                config = result.get('config')
                if (not isinstance(config, str) or not config.startswith('[Interface]\n')
                        or len(config) > 2048 or result.get('issued') is not True):
                    raise ValidationError({'preview': 'Le fichier WireGuard n’a pas été généré.'})
            response = HttpResponse(config, content_type='application/octet-stream')
            response['Content-Disposition'] = f'attachment; filename="ak-{env.pk.hex[:10]}.conf"'
            response['Cache-Control'] = 'no-store, private, max-age=0'
            response['Pragma'] = 'no-cache'
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        except Exception as exc:
            return handle_error(exc, 'preview_credentials_issue')

    def delete(self, request, project_id, environment_id):
        try:
            with transaction.atomic():
                env = authorized(request.user, project_id, environment_id, mutate=True)
                args, _ = payload(env)
                if not env.wireguard_only:
                    raise ValidationError({'preview': 'La préproduction n’est pas en mode privé.'})
                result = request_control('preview.credentials', {**args, 'operation': 'revoke'}, timeout=30)
                if result.get('issued') is not False:
                    raise ValidationError({'preview': 'Révocation non confirmée.'})
            return api_success({'issued': False}, 'Accès WireGuard révoqué.')
        except Exception as exc:
            return handle_error(exc, 'preview_credentials_revoke')
