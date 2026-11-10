from django.db import transaction
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ValidationError
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.configurations.serializers import DeploymentConfigSerializer, DeploymentConfigWriteSerializer
from apps.projects.configurations.services import (get_config, create_config, check_configuration,
    validate_branch, check_entitlement, managed_internal_port)
from apps.plans.catalog.models import ResourcePlan, DeploymentModeOffer
from apps.plans.entitlements.models import HighAvailabilityOption
from helpers.responses import api_success
from helpers.errors import handle_error
from apps.projects.configurations.swagger.config_list import schema as config_list_schema
from apps.projects.configurations.swagger.config_create import schema as config_create_schema
from apps.projects.configurations.swagger.config_detail import schema as config_detail_schema
from apps.projects.configurations.swagger.config_update import schema as config_update_schema
from apps.projects.configurations.swagger.config_finalize import schema as config_finalize_schema
from apps.projects.configurations.swagger.offer_choices import schema as offer_choices_schema


def validate_selected_plan(attrs):
    if 'resource_plan' not in attrs:
        return attrs
    plan_id = attrs['resource_plan']
    if plan_id is None:
        attrs['resource_plan'] = None
    else:
        plan = ResourcePlan.objects.filter(pk=plan_id, is_active=True).first()
        if plan is None:
            raise ValidationError({'resource_plan': 'Plan de ressources introuvable.'})
        attrs['resource_plan'] = plan
    return attrs


class ConfigurationListAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @config_list_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            return api_success({'configurations': DeploymentConfigSerializer(
                env.deployment_configs.order_by('-version'), many=True).data}, 'Configurations du déploiement.')
        except Exception as exc:
            return handle_error(exc, 'config_list')


class ConfigurationCreateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @config_create_schema
    def post(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id, write=True)
            serializer = DeploymentConfigWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            cfg = create_config(request.user, env, validate_selected_plan(serializer.validated_data))
            return api_success({'configuration': DeploymentConfigSerializer(cfg).data}, 'Brouillon de configuration créé.', 201)
        except Exception as exc:
            return handle_error(exc, 'config_create')


class ConfigurationDetailAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @config_detail_schema
    def get(self, request, project_id, environment_id, configuration_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            cfg = get_config(env, configuration_id)
            return api_success({'configuration': DeploymentConfigSerializer(cfg).data}, 'Configuration consultée.')
        except Exception as exc:
            return handle_error(exc, 'config_detail')


class ConfigurationUpdateAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @config_update_schema
    def patch(self, request, project_id, environment_id, configuration_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id, write=True)
            cfg = get_config(env, configuration_id)
            if not cfg.is_draft:
                raise ValidationError('Cette configuration est finalisée : créer une nouvelle version.')
            serializer = DeploymentConfigWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if not serializer.validated_data:
                raise ValidationError('Aucune modification fournie.')
            update = validate_selected_plan(serializer.validated_data)
            for key, value in update.items():
                setattr(cfg, key, value)
            managed_port = managed_internal_port(cfg.build_method, cfg.settings)
            fields = set(update)
            if managed_port is not None:
                cfg.internal_port = managed_port
                fields.add('internal_port')
            cfg.save(update_fields=list(fields))
            return api_success({'configuration': DeploymentConfigSerializer(cfg).data}, 'Brouillon modifié.')
        except Exception as exc:
            return handle_error(exc, 'config_update')


class ConfigurationFinalizeAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @config_finalize_schema
    def post(self, request, project_id, environment_id, configuration_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id, write=True)
            cfg = get_config(env, configuration_id)
            if not cfg.is_draft:
                raise ValidationError('Configuration déjà finalisée.')
            issues = check_configuration(cfg)
            if issues:
                raise ValidationError(issues)
            check_entitlement(cfg)
            validate_branch(request.user, cfg)
            cfg.is_draft = False
            cfg.save(update_fields=['is_draft'])
            return api_success({'configuration': DeploymentConfigSerializer(cfg).data},
                               'Configuration prête ; aucun déploiement lancé à cette étape.')
        except Exception as exc:
            return handle_error(exc, 'config_finalize')


class ConfigurationOffersAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    @offer_choices_schema
    def get(self, request, project_id, environment_id):
        try:
            _, env = get_environment(request.user, project_id, environment_id)
            entitlement = getattr(env, 'entitlement', None)
            ha_option = HighAvailabilityOption.objects.filter(environment=env).first()
            return api_success({'modes': [
                {'id': str(o.id), 'code': o.code, 'name': o.name, 'is_premium': o.is_premium}
                for o in DeploymentModeOffer.objects.filter(is_active=True).order_by('code')],
                'resource_plans': [{'id': str(p.id), 'code': p.code, 'name': p.name,
                    'cpu_millicores': p.cpu_millicores, 'memory_mb': p.memory_mb,
                    'storage_mb': p.storage_mb, 'max_instances': p.max_instances, 'is_premium': p.is_premium}
                    for p in ResourcePlan.objects.filter(is_active=True).order_by('display_order', 'name')],
                'entitlement': {'mode': entitlement.mode_offer.code,
                    'resource_plan': str(entitlement.resource_plan_id),
                    'resource_plan_code': entitlement.resource_plan.code,
                    'max_instances': entitlement.resource_plan.max_instances,
                    'status': entitlement.status, 'grant_source': entitlement.grant_source,
                    'is_sandbox': entitlement.grant_source == 'TEST'}
                    if entitlement else None,
                'ha_option': ({'status': ha_option.status,
                               'desired_replicas': ha_option.desired_replicas}
                              if ha_option else None)}, 'Offres et limites disponibles.')
        except Exception as exc:
            return handle_error(exc, 'config_offers')
