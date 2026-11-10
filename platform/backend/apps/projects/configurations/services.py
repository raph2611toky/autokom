from django.db import transaction
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.git.services import get_branches


def get_config(env, config_id):
    cfg = DeploymentConfig.objects.filter(environment=env, pk=config_id).first()
    if cfg is None:
        raise NotFound('Configuration introuvable.')
    return cfg


ALLOWED_STATIC_PROFILES = {'vite', 'npm_static', 'next_export', 'html'}
BACKEND_RUNTIMES = {
    **dict.fromkeys(('python_fastapi', 'python_flask', 'python_django', 'python_wsgi'), 'PYTHON'),
    **dict.fromkeys(('node_express', 'node_nest'), 'NODE'),
    **dict.fromkeys(('java_spring_maven', 'java_spring_gradle'), 'JAVA'),
    **dict.fromkeys(('php_plain', 'php_laravel', 'php_symfony'), 'PHP'),
}

_NPM_SCRIPT = __import__('re').compile(r'^npm run [A-Za-z][A-Za-z0-9:_-]{0,63}$')


def managed_internal_port(build_method, settings):
    """New managed backend configurations use 8000, frontend uses 8080.

    Dockerfile (user-supplied) configurations are not rewritten.
    Existing finalized configurations and their deployed endpoints are untouched.
    """
    options = settings if isinstance(settings, dict) else {}
    if build_method == DeploymentConfig.BuildMethod.AUTO and options.get('frontend_profile') in BACKEND_RUNTIMES:
        return 8000
    if build_method in (DeploymentConfig.BuildMethod.STATIC, DeploymentConfig.BuildMethod.AUTO):
        return 8080
    return None


def check_configuration(cfg):
    """Explicitly allow only the fixed trusted frontend recipes in the isolated runner."""
    issues = {}
    options = cfg.settings if isinstance(cfg.settings, dict) else {}
    profile = options.get('frontend_profile', 'vite')
    if not isinstance(profile, str):
        return {'settings': 'Recette frontend invalide.'}
    if cfg.build_method == DeploymentConfig.BuildMethod.STATIC:
        outputs = {'vite': {'dist'}, 'npm_static': {'dist', 'build'},
                   'next_export': {'out'}, 'html': {''}}
        if (profile not in ALLOWED_STATIC_PROFILES or cfg.runtime_type != DeploymentConfig.RuntimeType.STATIC or
                cfg.output_directory not in outputs.get(profile, set()) or cfg.internal_port != 8080):
            issues['static'] = 'La recette frontend, son dossier de sortie et le port 8080 doivent correspondre.'
        if cfg.start_command:
            issues['start_command'] = 'Le site statique est servi automatiquement par Nginx.'
        if cfg.build_command and (profile == 'html' or not _NPM_SCRIPT.fullmatch(cfg.build_command)):
            issues['build_command'] = 'Commande de build non autorisée pour cette recette.'
    elif cfg.build_method == DeploymentConfig.BuildMethod.AUTO:
        if profile == 'next_server':
            if (cfg.runtime_type != DeploymentConfig.RuntimeType.NODE or
                    cfg.internal_port != 8080 or cfg.output_directory not in ('', '.next')):
                issues['build_method'] = 'La recette Next.js doit utiliser Node sur le port 8080.'
            if cfg.build_command and not _NPM_SCRIPT.fullmatch(cfg.build_command):
                issues['build_command'] = 'Utilisez un script npm run NOM.'
            if cfg.start_command not in ('', 'npm run start'):
                issues['start_command'] = 'Démarrage Next.js : npm run start.'
        elif profile in BACKEND_RUNTIMES:
            if (cfg.runtime_type != BACKEND_RUNTIMES[profile] or
                    cfg.internal_port != 8000 or
                    cfg.output_directory or cfg.build_command or cfg.start_command):
                issues['build_method'] = 'Profil backend : port interne 8000, sans commande personnalisée.'
        else:
            issues['build_method'] = 'Recette serveur non autorisée.'
    elif cfg.build_method == DeploymentConfig.BuildMethod.DOCKERFILE:
        if not cfg.dockerfile_path:
            issues['dockerfile_path'] = 'Indiquer le chemin du Dockerfile.'
        if cfg.build_command or cfg.start_command:
            issues['commands'] = 'Définir les commandes directement dans le Dockerfile.'
    else:
        issues['build_method'] = 'Buildpacks non encore pris en charge.'
    if options.get('install_command') not in (None, '', 'npm ci'):
        issues['settings'] = 'La recette approuvée installe les dépendances avec npm ci.'
    if cfg.auto_deploy and not (cfg.auto_deploy_branch or cfg.branch):
        issues['auto_deploy_branch'] = 'Branche automatique requise.'
    health = cfg.healthcheck_path
    if (not isinstance(health, str) or not health.startswith('/') or
            len(health) > 128 or any(c in health for c in ('?', '#', '%', chr(92), chr(13), chr(10)))):
        issues['healthcheck_path'] = 'Chemin HTTP local invalide pour le broker AUTOKOM.'
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
    port = managed_internal_port(data.get('build_method'), data.get('settings'))
    if port is not None:
        data['internal_port'] = port
    return DeploymentConfig.objects.create(environment=env, created_by=user,
        version=(latest.version+1) if latest else 1, is_draft=True, **data)


def check_entitlement(cfg):
    """Draft may request premium; finalization requires an actual granted entitlement."""
    from rest_framework.exceptions import PermissionDenied
    from django.utils import timezone
    entitlement = getattr(cfg.environment, 'entitlement', None)
    if (entitlement is None or entitlement.status != 'ACTIVE' or
            (entitlement.expires_at and entitlement.expires_at <= timezone.now())):
        raise PermissionDenied('Aucun plan actif attribué à cet environnement.')
    if getattr(entitlement, 'grant_source', None) == 'TEST':
        from django.conf import settings
        from apps.plans.entitlements.models import SandboxCheckout
        receipt = entitlement.sandbox_checkout if entitlement.sandbox_checkout_id else None
        if (not settings.AUTOKOM_BILLING_SANDBOX_ENABLED or receipt is None or
                receipt.status != SandboxCheckout.Status.ACCEPTED or
                receipt.environment_id != cfg.environment_id or
                receipt.resource_plan_id != entitlement.resource_plan_id or
                receipt.expires_at <= timezone.now()):
            raise PermissionDenied('Abonnement TEST désactivé, expiré ou non valide.')
    # Free plan is still limited by assigned CPU/RAM/quota; only fixed, non-Dockerfile
    # frontend recipes are allowed. Custom services remain paid.
    # A client must not gain a paid backend runtime by editing request JSON.
    if not entitlement.resource_plan.is_premium and cfg.build_method not in (DeploymentConfig.BuildMethod.STATIC, DeploymentConfig.BuildMethod.AUTO):
        raise PermissionDenied('L’offre Découverte autorise les recettes frontend approuvées, sans Dockerfile personnalisé.')
    if cfg.mode == 'DEVSECOPS' and entitlement.mode_offer.code != 'DEVSECOPS':
        raise PermissionDenied('Le mode DevSecOps nécessite une autorisation premium.')
    chosen = cfg.resource_plan or entitlement.resource_plan
    granted = entitlement.resource_plan
    if (chosen.cpu_millicores > granted.cpu_millicores or chosen.memory_mb > granted.memory_mb or
        chosen.storage_mb > granted.storage_mb or chosen.max_instances > granted.max_instances):
        raise PermissionDenied('Les ressources choisies dépassent le plan autorisé.')
    if getattr(cfg, 'high_availability_requested', False):
        from apps.plans.entitlements.models import HighAvailabilityOption
        option = HighAvailabilityOption.objects.filter(environment=cfg.environment).first()
        if granted.max_instances < 2:
            raise PermissionDenied(
                'Haute disponibilité non autorisée : le plan réellement attribué à cet environnement '
                'ne permet qu’une seule instance. La sélection Professionnel sur le formulaire ne '
                'change pas les droits. Désactivez l’option HA pour déployer en standard, ou demandez '
                'l’attribution administrative d’un plan à deux instances et de l’option HA.'
            )
        if chosen.max_instances < 2:
            raise PermissionDenied(
                'Haute disponibilité non autorisée : le profil de ressources sélectionné dans la '
                'configuration ne prévoit qu’une instance, malgré un droit supérieur. '
                'Sélectionnez un profil autorisé à deux instances.'
            )
        if (option is None or option.status != HighAvailabilityOption.Status.ACTIVE or
                (option.expires_at and option.expires_at <= timezone.now()) or
                (getattr(entitlement, 'grant_source', None) == 'TEST' and option.sandbox_checkout_id != entitlement.sandbox_checkout_id)):
            raise PermissionDenied('Haute disponibilite demandee, mais option non active ou non accordee. La configuration reste en brouillon.')
    return chosen
