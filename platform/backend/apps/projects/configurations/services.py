from django.db import transaction
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.git.services import get_branches


def get_config(env, config_id):
    cfg = DeploymentConfig.objects.filter(environment=env, pk=config_id).first()
    if cfg is None:
        raise NotFound('Configuration introuvable.')
    return cfg


def check_configuration(cfg):
    issues = {}
    if cfg.build_method == DeploymentConfig.BuildMethod.STATIC and not cfg.output_directory:
        issues['output_directory'] = 'Répertoire de sortie obligatoire pour les sites statiques.'
    if cfg.build_method == DeploymentConfig.BuildMethod.DOCKERFILE and not cfg.dockerfile_path:
        issues['dockerfile_path'] = 'Indiquer un Dockerfile.'
    if cfg.auto_deploy and not (cfg.auto_deploy_branch or cfg.branch):
        issues['auto_deploy_branch'] = 'Branche automatique requise.'
    if cfg.runtime_type == DeploymentConfig.RuntimeType.STATIC and cfg.start_command:
        issues['start_command'] = 'Une application statique ne nécessite pas de commande de démarrage.'
    return issues


def validate_branch(user, cfg):
    project = cfg.environment.project
    connection = project.git_connection
    if connection is None or connection.installation_id is None:
        raise ValidationError({'branch': 'Connexion GitHub absente.'})
    for page in range(1, 21):
        _, branches = get_branches(user, connection.installation_id, project.repository_external_id, page)
        if any(x.get('name') == cfg.branch for x in branches):
            return
        if len(branches) < 100:
            break
    raise ValidationError({'branch': 'Branche non trouvée parmi les branches autorisées.'})


@transaction.atomic
def create_config(user, env, attrs):
    latest = DeploymentConfig.objects.filter(environment=env).order_by('-version').first()
    if latest and latest.is_draft:
        raise ValidationError({'configuration': 'Un brouillon existe déjà. Modifiez-le ou finalisez-le.'})
    data = {field.name: getattr(latest, field.name) for field in DeploymentConfig._meta.fields
            if field.name not in ('id','created_at','version','environment','created_by','is_draft')} if latest else {}
    data.update(attrs)
    return DeploymentConfig.objects.create(environment=env, created_by=user,
        version=(latest.version+1) if latest else 1, is_draft=True, **data)


def check_entitlement(cfg):
    """Draft may request premium; finalization requires an actual granted entitlement."""
    from rest_framework.exceptions import PermissionDenied
    from apps.plans.catalog.models import ResourcePlan
    from django.utils import timezone
    entitlement = getattr(cfg.environment, 'entitlement', None)
    if (entitlement is None or entitlement.status != 'ACTIVE' or
            (entitlement.expires_at and entitlement.expires_at <= timezone.now())):
        raise PermissionDenied('Aucun plan actif attribué à cet environnement.')
    if cfg.mode == 'DEVSECOPS' and entitlement.mode_offer.code != 'DEVSECOPS':
        raise PermissionDenied('Le mode DevSecOps nécessite une autorisation premium.')
    chosen = cfg.resource_plan or entitlement.resource_plan
    granted = entitlement.resource_plan
    if (chosen.cpu_millicores > granted.cpu_millicores or chosen.memory_mb > granted.memory_mb or
        chosen.storage_mb > granted.storage_mb or chosen.max_instances > granted.max_instances):
        raise PermissionDenied('Les ressources choisies dépassent le plan autorisé.')
    return chosen
