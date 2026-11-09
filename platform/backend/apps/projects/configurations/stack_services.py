"""Opt-in two-service deployment. All existing single-service paths remain intact.

API receives only an allowlisted source profile and variable names; no arbitrary
Dockerfiles, shell commands, public ports or cross-project environments.
"""
import re
import secrets
import time
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.projects.common import get_project, safe_relative_path, validate_hostname
from apps.projects.configurations.models import DeploymentConfig, ServiceStack
from apps.projects.configurations.services import (
    BACKEND_RUNTIMES, check_configuration, check_entitlement, validate_branch, create_config,
)
from apps.projects.environments.models import Environment, EnvironmentVariable
from apps.projects.project.services import free_entitlement
from apps.projects.secrets.models import SecretReference
from apps.projects.git.services import encrypt
from apps.deployments.domains.models import Domain
from apps.deployments.domains.services import claim_domain, domain_summary, _challenge
from apps.deployments.runs.models import StackDeployment, DeploymentRun
from apps.deployments.runs.services import (
    plan_deployment, ensure_runner_ready, _select_branch_commit, _create_run,
    DeploymentConflict,
)

FRONT_PROFILES = {'vite', 'npm_static', 'html', 'next_export', 'next_server'}
ENV_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,159}$')
PUBLIC_PREFIXES = ('VITE_', 'REACT_APP_', 'VUE_APP_', 'NEXT_PUBLIC_')
DANGEROUS_KEYS = ('PASSWORD', 'SECRET', 'TOKEN', 'PRIVATE_KEY', 'API_KEY', 'DATABASE_URL', 'REDIS_URL')
MANAGED_KEY = re.compile(r'^[A-Z][A-Z0-9_]{0,99}$')


def _frontend_api_key(profile):
    prefix = ('NEXT_PUBLIC_' if profile.startswith('next_') else
              'REACT_APP_' if profile == 'npm_static' else 'VITE_')
    return prefix + 'API_URL'


def _valid_managed_name(name):
    return (bool(MANAGED_KEY.fullmatch(name)) and not name.startswith(('AUTOKOM_', 'DOCKER_')))


class StackComponentSerializer(serializers.Serializer):
    branch = serializers.CharField(max_length=255)
    root_directory = serializers.CharField(max_length=300, allow_blank=True, default='')
    profile = serializers.CharField(max_length=50)
    healthcheck = serializers.CharField(max_length=128, default='/')
    hostname = serializers.CharField(max_length=253, allow_blank=True, required=False, default='')
    # Accept legacy recipe outputs (.next, out), then validate per profile.
    # A Next.js SSR build produces .next internally: not a static publication directory.
    output_directory = serializers.ChoiceField(choices=['dist', 'build', '.next', 'out', ''],
                                               default='build', required=False, allow_blank=True)
    variables = serializers.DictField(child=serializers.CharField(allow_blank=True, max_length=32768), required=False)
    secrets = serializers.DictField(child=serializers.CharField(allow_blank=False, max_length=10000), required=False, write_only=True)
    api_env_key = serializers.CharField(max_length=100, allow_blank=True, default='', required=False)
    origin_env_key = serializers.CharField(max_length=100, allow_blank=True, default='', required=False)

    def validate_hostname(self, value):
        return validate_hostname(value) if value else ''

    def validate_branch(self, value):
        if not value.strip() or value.startswith('-') or '..' in value or '\x00' in value:
            raise serializers.ValidationError('Branche Git invalide.')
        return value

    def validate_root_directory(self, value):
        if value in ('.', './'):
            return ''
        return safe_relative_path(value)

    def validate_healthcheck(self, value):
        if not value.startswith('/') or any(x in value for x in ('?', '#', '%', '\\', '\r', '\n')):
            raise serializers.ValidationError('Chemin de vérification invalide.')
        return value

    def validate(self, attrs):
        kind = self.context['kind']
        profile = attrs['profile']
        if profile not in (FRONT_PROFILES if kind == 'frontend' else BACKEND_RUNTIMES):
            raise serializers.ValidationError({'profile': 'Recette non autorisée pour ce service.'})
        # Old single-service configurations may contain .next for SSR or out for
        # a static Next export; both are valid only with the matching recipe.
        # The actual published path is always derived by _component_config.
        compatible_outputs = ({
            'vite': {'', 'build', 'dist'},
            'npm_static': {'build', 'dist'},
            'next_server': {'', 'build', '.next'},
            'next_export': {'', 'build', 'out'},
            'html': {'', 'build'},
        }[profile] if kind == 'frontend' else {'', 'build'})
        if attrs['output_directory'] not in compatible_outputs:
            raise serializers.ValidationError({'output_directory': 'Dossier incompatible avec la recette sélectionnée.'})
        values, secrets_dict = attrs.get('variables', {}), attrs.get('secrets', {})
        if len(values) + len(secrets_dict) > 50 or set(values) & set(secrets_dict):
            raise serializers.ValidationError('Variables dupliquées ou trop nombreuses.')
        reserved = ({'PORT', 'FRONTEND_ORIGIN', 'NODE_ENV', 'BACKEND_INTERNAL_URL'} if kind == 'backend'
                    else {'VITE_API_URL', 'REACT_APP_API_URL', 'VUE_APP_API_URL', 'NEXT_PUBLIC_API_URL'})
        if reserved.intersection(values) or reserved.intersection(secrets_dict):
            raise serializers.ValidationError({'variables': 'Variable gérée automatiquement par AUTOKOM : ne pas la modifier.'})
        # Only the name is chosen by the user; the value always comes from the
        # validated domain or the fixed internal port, never from the browser.
        if kind == 'frontend':
            generated = attrs.get('api_env_key') or _frontend_api_key(profile)
            allowed = (('NEXT_PUBLIC_',) if profile.startswith('next_') else
                       ('REACT_APP_', 'VUE_APP_', 'VITE_') if profile == 'npm_static' else ('VITE_',))
            if (not _valid_managed_name(generated) or
                    not any(generated.startswith(prefix) and len(generated) > len(prefix) for prefix in allowed) or
                    any(marker in generated for marker in ('SECRET', 'PASSWORD', 'TOKEN', 'PRIVATE', 'CREDENTIAL',
                                                            'API_KEY', 'ACCESS_KEY', 'SESSION', 'AUTH_KEY'))):
                raise serializers.ValidationError({'api_env_key': 'Nom de variable publique invalide pour ce profil.'})
            attrs['api_env_key'] = generated
        else:
            generated = attrs.get('origin_env_key') or 'FRONTEND_ORIGIN'
            if (not _valid_managed_name(generated) or generated in reserved - {'FRONTEND_ORIGIN'} or
                    any(marker in generated for marker in DANGEROUS_KEYS)):
                raise serializers.ValidationError({'origin_env_key': 'Nom de variable backend invalide.'})
            attrs['origin_env_key'] = generated
        if generated in values or generated in secrets_dict:
            raise serializers.ValidationError({'variables': 'Nom réservé par une variable automatique.'})
        for name, value in {**values, **secrets_dict}.items():
            if not ENV_KEY.fullmatch(name) or name.startswith(('AUTOKOM_', 'DOCKER_')):
                raise serializers.ValidationError({'variables': 'Nom de variable réservé ou invalide.'})
            if '\x00' in value or '\n' in value or '\r' in value:
                raise serializers.ValidationError({'variables': 'Une valeur par ligne est obligatoire.'})
            if kind == 'frontend' and (not name.startswith(PUBLIC_PREFIXES) or
                                       any(marker in name.upper() for marker in DANGEROUS_KEYS)):
                raise serializers.ValidationError({'variables': 'Le frontend n’accepte que des variables publiques non sensibles.'})
            if kind == 'backend' and name in values and any(marker in name.upper() for marker in DANGEROUS_KEYS):
                raise serializers.ValidationError({'variables': 'Utilisez le champ secret chiffré pour cette variable.'})
        if kind == 'frontend' and secrets_dict:
            raise serializers.ValidationError({'secrets': 'Aucun secret dans le bundle JavaScript.'})
        if sum(len(v) for v in list(values.values()) + list(secrets_dict.values())) > 32768:
            raise serializers.ValidationError('Variables trop volumineuses.')
        return attrs


class StackConfigureSerializer(serializers.Serializer):
    frontend = serializers.DictField()
    backend = serializers.DictField()
    auto_deploy = serializers.BooleanField(default=False)

    def validate(self, attrs):
        for kind in ('frontend', 'backend'):
            fields = attrs[kind]
            validate_serializer = StackComponentSerializer(data=fields, context={'kind': kind})
            validate_serializer.is_valid(raise_exception=True)
            attrs[kind] = validate_serializer.validated_data
        return attrs


def _component_config(env, user, data, kind):
    previous = DeploymentConfig.objects.filter(environment=env).order_by('-version').first()
    if previous and previous.is_draft:
        cfg = previous
    else:
        cfg = create_config(user, env, {})
    profile = data['profile']
    cfg.branch = data['branch']
    cfg.root_directory = data['root_directory']
    cfg.mode = 'DEVOPS'
    # Only the opt-in stack backend listens on 8000. Legacy services stay on 8080.
    cfg.internal_port = 8000 if kind == 'backend' else 8080
    cfg.healthcheck_path = data['healthcheck']
    cfg.auto_deploy = False  # Stack pushes are coordinated, never standalone.
    cfg.auto_deploy_branch = ''
    cfg.settings = {'frontend_profile': profile, 'stack_hostname': data['hostname'],
                    **({'stack_api_env_key': data['api_env_key']} if kind == 'frontend'
                       else {'stack_origin_env_key': data['origin_env_key']})}
    if kind == 'frontend':
        cfg.runtime_type = 'NODE' if profile == 'next_server' else 'STATIC'
        cfg.build_method = 'AUTO' if profile == 'next_server' else 'STATIC'
        cfg.output_directory = (data.get('output_directory', 'build') if profile == 'npm_static'
                                else {'vite': 'dist', 'next_export': 'out', 'html': '', 'next_server': ''}[profile])
        cfg.build_command = '' if profile == 'html' else 'npm run build'
        cfg.start_command = 'npm run start' if profile == 'next_server' else ''
    else:
        cfg.runtime_type = BACKEND_RUNTIMES[profile]
        cfg.build_method = 'AUTO'
        cfg.build_command = cfg.start_command = cfg.output_directory = ''
    cfg.save()
    issues = check_configuration(cfg)
    if issues:
        raise ValidationError({kind: issues})
    check_entitlement(cfg)
    cfg.is_draft = False
    cfg.save(update_fields=['is_draft'])
    return cfg


def _sync_variables(project, env, data, kind, *, api_url, previous_managed_key=None):
    """Persist generated .env values from trusted configuration, without exposing secrets."""
    values = dict(data.get('variables', {}))
    if kind == 'frontend':
        generated_key = data['api_env_key']
        # Always point at the public HTTPS backend, never an internal Docker IP.
        values[generated_key] = 'https://' + api_url + '/api'
    else:
        # PORT is immutable even if an imported .env tries to override it.
        generated_key = data['origin_env_key']
        values['PORT'] = '8000'
        values[generated_key] = 'https://' + api_url
        if data['profile'].startswith('node_'):
            values['NODE_ENV'] = 'production'
    # Renaming a managed key should not leave the previous generated default in
    # the build/runtime environment. Never delete other keys or secret entries.
    if previous_managed_key and previous_managed_key != generated_key:
        EnvironmentVariable.objects.filter(environment=env, key=previous_managed_key,
                                           secret__isnull=True).delete()
    for key, value in values.items():
        item, _ = EnvironmentVariable.objects.get_or_create(environment=env, key=key)
        if item.secret_id:
            raise ValidationError({'variables': f'Variable {key} déjà secrète : modifier via les secrets.'})
        item.value = value
        item.save(update_fields=['value', 'updated_at'])
    for key, value in data.get('secrets', {}).items():
        existing = SecretReference.objects.filter(project=project, environment=env, name=key).first()
        if existing:
            existing.encrypted_value = encrypt(value)
            existing.is_active = True
            existing.version_ref = str(int(existing.version_ref or '0') + 1)
            existing.save(update_fields=['encrypted_value', 'is_active', 'version_ref', 'updated_at'])
        else:
            existing = SecretReference.objects.create(
                project=project, organization=project.organization, environment=env,
                scope=SecretReference.Scope.ENVIRONMENT, name=key,
                encrypted_value=encrypt(value), version_ref='1',
            )
        item, _ = EnvironmentVariable.objects.get_or_create(environment=env, key=key)
        item.value, item.secret = '', existing
        item.save(update_fields=['value', 'secret', 'updated_at'])


def stack_domain_hostname(environment, cfg):
    """Current selected name, with compatibility for stacks saved before host editing."""
    configured = (cfg.settings or {}).get('stack_hostname') if cfg else None
    if configured:
        return configured
    legacy = (Domain.objects.filter(environment=environment, is_primary=True).first()
              or Domain.objects.filter(environment=environment, domain_type=Domain.Type.SUBDOMAIN)
                   .order_by('created_at').first()
              or Domain.objects.filter(environment=environment).order_by('created_at').first())
    return legacy.hostname if legacy else ''


def stack_summary(stack):
    fcfg = DeploymentConfig.objects.filter(environment=stack.frontend_environment, is_draft=False).order_by('-version').first()
    bcfg = DeploymentConfig.objects.filter(environment=stack.backend_environment, is_draft=False).order_by('-version').first()
    fhost = stack_domain_hostname(stack.frontend_environment, fcfg)
    bhost = stack_domain_hostname(stack.backend_environment, bcfg)
    fdomain = Domain.objects.filter(environment=stack.frontend_environment, hostname=fhost).first()
    bdomain = Domain.objects.filter(environment=stack.backend_environment, hostname=bhost).first()
    latest = stack.deployments.order_by('-created_at').first()
    fvars = list(EnvironmentVariable.objects.filter(environment=stack.frontend_environment, secret__isnull=True)
                 .order_by('key').values('key', 'value'))
    bvars = list(EnvironmentVariable.objects.filter(environment=stack.backend_environment, secret__isnull=True)
                 .order_by('key').values('key', 'value'))
    bsecrets = list(EnvironmentVariable.objects.filter(environment=stack.backend_environment, secret__isnull=False)
                    .order_by('key').values_list('key', flat=True))
    return {
        'id': str(stack.id), 'project_id': str(stack.project_id),
        'auto_deploy': stack.auto_deploy,
        'frontend': {'environment_id': str(stack.frontend_environment_id),
                     'branch': fcfg.branch if fcfg else '', 'root_directory': fcfg.root_directory if fcfg else '',
                     'profile': (fcfg.settings or {}).get('frontend_profile') if fcfg else '',
                     'api_env_key': ((fcfg.settings or {}).get('stack_api_env_key') or
                                     _frontend_api_key((fcfg.settings or {}).get('frontend_profile', '')))
                                    if fcfg else '',
                     'domain': fdomain.hostname if fdomain else '',
                     'verification': domain_summary(fdomain, include_challenge=True).get('verification') if fdomain else None,
                     'variables': fvars},
        'backend': {'environment_id': str(stack.backend_environment_id),
                    'branch': bcfg.branch if bcfg else '', 'root_directory': bcfg.root_directory if bcfg else '',
                    'profile': (bcfg.settings or {}).get('frontend_profile') if bcfg else '',
                    'origin_env_key': (bcfg.settings or {}).get('stack_origin_env_key', 'FRONTEND_ORIGIN') if bcfg else '',
                    'domain': bdomain.hostname if bdomain else '',
                    'verification': domain_summary(bdomain, include_challenge=True).get('verification') if bdomain else None,
                    'internal_port': bcfg.internal_port if bcfg else 8080, 'variables': bvars, 'secret_names': bsecrets},
        'latest': {'id': str(latest.id), 'status': latest.status,
                   'backend_run_id': str(latest.backend_run_id),
                   'frontend_run_id': str(latest.frontend_run_id) if latest.frontend_run_id else None,
                   'error_code': latest.error_code} if latest else None,
    }


def _primary_service_is_backend(environment):
    """Detect the legacy service kind without trusting an incoming browser flag."""
    previous = (DeploymentConfig.objects.filter(environment=environment, is_draft=False)
                .order_by('-version').first())
    if previous is None:
        return False
    profile = (previous.settings or {}).get('frontend_profile', '')
    if profile:
        return profile in BACKEND_RUNTIMES
    return previous.runtime_type in ('PYTHON', 'JAVA', 'PHP')


def configure_stack(user, project_id, attrs):
    """Atomic DB configuration; no Docker, Nginx or network action inside HTTP."""
    project = get_project(user, project_id, write=True)
    if project.status != 'ACTIVE' or not project.git_connection or not project.git_connection.is_active:
        raise ValidationError('Projet actif connecté à GitHub requis.')
    if project.pk is None:
        raise ValidationError('Projet invalide.')
    # Resolve and validate hostnames before reserving any domain.
    from django.conf import settings
    from apps.projects.common import default_hostname
    from apps.deployments.domains.services import _managed
    from apps.deployments.runs.autokom_socket import request_control
    default_front = default_hostname(project.slug)
    fhost = validate_hostname(attrs['frontend'].get('hostname') or default_front)
    bhost = validate_hostname(attrs['backend'].get('hostname') or ('backend.' + fhost))
    if fhost == bhost:
        raise ValidationError({'domain': 'Le frontend et le backend doivent avoir deux domaines distincts.'})
    # Only the configured managed zone may be created automatically. External
    # domains require the existing TXT ownership proof before publication.
    managed = [host for host in (fhost,bhost) if _managed(host)]
    if managed:
        request_control('domain.config.check', {'managed_suffix': settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX,
                                                'managed_hosts': managed}, timeout=15)
    attrs['frontend']['hostname'], attrs['backend']['hostname'] = fhost, bhost
    # Remote branch verification is performed before database write/locks.
    # This validates that both commits belong to the authorized installation.
    installation_id = project.git_connection.installation_id
    from apps.projects.git.services import get_branches
    for kind in ('frontend', 'backend'):
        branch = attrs[kind]['branch']
        found = False
        for page in range(1, 21):
            _, result = get_branches(user, installation_id, project.repository_external_id, page)
            if any(item.get('name') == branch for item in result):
                found = True; break
            if len(result) < 100:
                break
        if not found:
            raise ValidationError({kind: 'Branche introuvable ou non autorisée.'})
    with transaction.atomic():
        project = type(project).objects.select_for_update().get(pk=project.pk)
        existing = ServiceStack.objects.select_for_update().filter(project=project).first()
        if existing:
            if existing.deployments.filter(status__in=['QUEUED', 'PREPARING', 'ACTIVATING', 'BLOCKED']).exists():
                raise DeploymentConflict('Déploiement de deux services en cours : aucune modification autorisée.')
            frontend_env = existing.frontend_environment
            backend_env = existing.backend_environment
            stack = existing
        else:
            frontend_env = Environment.objects.filter(project=project, slug='production').first()
            if frontend_env is None:
                raise ValidationError('Environnement de production introuvable.')
            if Environment.objects.filter(project=project, slug='backend-production').exists():
                raise ValidationError('Un environnement backend non lié est déjà présent.')
            backend_env = Environment.objects.create(project=project, name='Backend · Production',
                                                     slug='backend-production', kind='PRODUCTION')
            free_entitlement(backend_env)
            if _primary_service_is_backend(frontend_env):
                # A backend-only project is becoming a pair. Preserve its
                # existing encrypted environment secrets and plain variables
                # in the new backend environment, without ever exposing them
                # to the newly created frontend. Entire operation is atomic.
                SecretReference.objects.filter(project=project, environment=frontend_env).update(
                    environment=backend_env)
                EnvironmentVariable.objects.filter(environment=frontend_env).update(
                    environment=backend_env)
            stack = ServiceStack.objects.create(project=project, frontend_environment=frontend_env,
                                                backend_environment=backend_env)
        previous_front = (DeploymentConfig.objects.filter(environment=frontend_env, is_draft=False)
                          .order_by('-version').first())
        previous_back = (DeploymentConfig.objects.filter(environment=backend_env, is_draft=False)
                         .order_by('-version').first())
        previous_front_key = (previous_front.settings or {}).get('stack_api_env_key') if previous_front else None
        if previous_front and not previous_front_key and (previous_front.settings or {}).get('stack_hostname'):
            previous_front_key = _frontend_api_key((previous_front.settings or {}).get('frontend_profile', ''))
        previous_back_key = (previous_back.settings or {}).get('stack_origin_env_key', 'FRONTEND_ORIGIN') if previous_back and (previous_back.settings or {}).get('stack_hostname') else None
        frontend_cfg = _component_config(frontend_env, user, attrs['frontend'], 'frontend')
        backend_cfg = _component_config(backend_env, user, attrs['backend'], 'backend')
        # Claim both names under their own environment. An existing domain in a
        # different project/environment is never adopted or overwritten.
        for kind, env, host in (('frontend', frontend_env, fhost), ('backend', backend_env, bhost)):
            if not Domain.objects.filter(environment=env, hostname=host).exists():
                claim_domain(user, project.pk, env.pk, host,
                             Domain.Type.SUBDOMAIN if _managed(host) else Domain.Type.CUSTOM)
        _sync_variables(project, frontend_env, attrs['frontend'], 'frontend', api_url=bhost,
                        previous_managed_key=previous_front_key)
        _sync_variables(project, backend_env, attrs['backend'], 'backend', api_url=fhost,
                        previous_managed_key=previous_back_key)
        stack.auto_deploy = attrs['auto_deploy']
        stack.save(update_fields=['auto_deploy', 'updated_at'])
        return stack


def preflight_stack_domains(stack):
    """Verify both owned hostnames against the public origin before a build.

    Managed DNS can be created by the root-only broker. External domains must
    already have a matching _autokom-challenge TXT proof and an A record to the
    correct ingress IP. A proxied Cloudflare record intentionally does not pass
    the existing broker check (it resolves to Cloudflare rather than origin).
    """
    from apps.deployments.runs.autokom_socket import request_control
    from apps.deployments.domains.services import _challenge, _managed
    checked = []
    for kind, env in (('backend', stack.backend_environment),
                      ('frontend', stack.frontend_environment)):
        cfg = (DeploymentConfig.objects.filter(environment=env, is_draft=False)
               .order_by('-version').first())
        host = stack_domain_hostname(env, cfg)
        domain = Domain.objects.filter(environment=env, hostname=host, is_disabled=False).first()
        if not domain or domain.organization_id != stack.project.organization_id:
            raise ValidationError({kind: 'Domaine non réservé pour ce service.'})
        managed = _managed(domain.hostname)
        if (domain.domain_type == Domain.Type.SUBDOMAIN) != managed:
            raise ValidationError({kind: 'Type de domaine incompatible avec sa zone DNS.'})
        if managed:
            request_control('domain.dns.create', {'hostname': domain.hostname}, timeout=25)
        from apps.deployments.runs.autokom_socket import AutokomControlUnavailable
        dns_result = None
        for attempt in range(4 if managed else 1):
            try:
                dns_result = request_control('domain.verify', {
                    'hostname': domain.hostname, 'managed': managed,
                    'token': _challenge(domain),
                }, timeout=25)
                break
            except AutokomControlUnavailable:
                if attempt == (3 if managed else 0):
                    raise
                time.sleep((attempt + 1) * 3)
        checked.append({'service': kind, 'hostname': domain.hostname, 'verified': True,
                        'ipv4': dns_result.get('ipv4') if dns_result else None})
    return checked


def _split_resources(backend, frontend):
    """Two containers must share the granted resource envelope, not double it."""
    for key, minimum in (('cpu_millicores', 100), ('memory_mb', 128), ('storage_mb', 128)):
        if backend[key] != frontend[key] or backend[key] < 2 * minimum:
            raise PermissionDenied('Offre insuffisante pour deux services isolés : choisir un plan adapté.')
    result = {}
    for key in ('cpu_millicores', 'memory_mb', 'storage_mb'):
        result[key] = backend[key] // 2
    result.update(resource_plan=backend['resource_plan'], mode=backend['mode'], replicas=1)
    return result


def launch_stack(user, stack, *, idempotency_key=None, trigger=DeploymentRun.Trigger.MANUAL,
                 event=None, commits=None):
    """Queue both isolated builds atomically; promote only after both are healthy."""
    from apps.deployments.runs.services import IDEMPOTENCY_RE
    if idempotency_key and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise ValidationError('Clé d’idempotence invalide.')
    if idempotency_key is None:
        idempotency_key = secrets.token_urlsafe(24)
    idem = f'stack:{stack.pk}:{idempotency_key}'
    if len(idem) + len(':frontend') > 150:
        raise ValidationError('Clé d’idempotence trop longue pour un déploiement groupé.')
    existing = StackDeployment.objects.filter(idempotency_key=idem).first()
    if existing:
        return existing, False
    # Enforce DNS/ownership checks server-side, even when the UI was bypassed.
    preflight_stack_domains(stack)
    backend_cfg, back_resources = plan_deployment(stack.backend_environment)
    frontend_cfg, front_resources = plan_deployment(stack.frontend_environment)
    resources = _split_resources(back_resources, front_resources)
    ensure_runner_ready()
    if commits:
        front_sha, back_sha = commits
    else:
        front_sha = _select_branch_commit(user, frontend_cfg)
        back_sha = _select_branch_commit(user, backend_cfg)
    with transaction.atomic():
        ServiceStack.objects.select_for_update().get(pk=stack.pk)
        existing = StackDeployment.objects.filter(idempotency_key=idem).first()
        if existing:
            return existing, False
        if StackDeployment.objects.filter(stack=stack, status__in=['QUEUED', 'PREPARING', 'ACTIVATING', 'BLOCKED']).exists():
            raise DeploymentConflict('Une publication de ce projet est déjà en cours.')
        if DeploymentRun.objects.filter(environment__in=[stack.frontend_environment, stack.backend_environment],
                                        status__in=['QUEUED', 'RUNNING']).exists():
            raise DeploymentConflict('Un service est déjà en cours de déploiement.')
        if (backend_cfg.pk != DeploymentConfig.objects.filter(environment=stack.backend_environment, is_draft=False).order_by('-version').first().pk or
                frontend_cfg.pk != DeploymentConfig.objects.filter(environment=stack.frontend_environment, is_draft=False).order_by('-version').first().pk):
            raise DeploymentConflict('Configuration modifiée pendant la validation.')
        # Two durable, independent Jenkins runs are created inside the SAME
        # transaction. If either insert fails, neither deployment is queued.
        backend_run = _create_run(stack.backend_environment, backend_cfg, resources,
                                  actor=user, trigger=trigger, sha=back_sha,
                                  idem=idem + ':backend', event=event)
        frontend_run = _create_run(stack.frontend_environment, frontend_cfg, resources,
                                   actor=user, trigger=trigger, sha=front_sha,
                                   idem=idem + ':frontend', event=event)
        group = StackDeployment.objects.create(
            stack=stack, backend_run=backend_run, frontend_run=frontend_run,
            frontend_configuration=frontend_cfg, frontend_sha=front_sha,
            frontend_resources=resources, idempotency_key=idem,
        )
        return group, True
