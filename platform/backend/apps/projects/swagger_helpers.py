"""Schémas OpenAPI 2 drf-yasg des enveloppes JSON réellement émises par les API Projects."""
from drf_yasg import openapi


def obj(fields, required=None, nullable=False):
    return openapi.Schema(type=openapi.TYPE_OBJECT, properties=fields,
                          required=list(fields) if required is None else required, x_nullable=nullable)


def arr(schema):
    return openapi.Schema(type=openapi.TYPE_ARRAY, items=schema)


def typ(kind, nullable=False):
    return openapi.Schema(type=kind, x_nullable=nullable)

S = typ(openapi.TYPE_STRING)
I = typ(openapi.TYPE_INTEGER)
B = typ(openapi.TYPE_BOOLEAN)
U = openapi.Schema(type=openapi.TYPE_STRING, format='uuid')
DT = openapi.Schema(type=openapi.TYPE_STRING, format='date-time')
N = typ(openapi.TYPE_OBJECT, nullable=True)
NS = typ(openapi.TYPE_STRING, nullable=True)
ANY = openapi.Schema(type=openapi.TYPE_OBJECT, x_nullable=True, description='Objet JSON quelconque (erreurs de validation ou métadonnées).')

PROJECT = obj({'id': U, 'organization': U, 'created_by': U, 'git_connection': openapi.Schema(type='string',format='uuid',x_nullable=True),
    'repository_external_id': S, 'repository_name': S, 'repository_url': S, 'default_branch': S,
    'name': S, 'slug': S, 'description': S, 'framework': S, 'status': S,
    'created_at': DT, 'updated_at': DT})
ENVIRONMENT = obj({'id': U, 'project': U, 'name': S, 'slug': S, 'kind': S,
    'is_active': B, 'created_at': DT, 'updated_at': DT})
VARIABLE = obj({'id': U, 'environment': U, 'key': S, 'value': S, 'secret_id': openapi.Schema(type='string',format='uuid',x_nullable=True),
    'created_at': DT, 'updated_at': DT})
DOMAIN = obj({'id': U, 'hostname': S, 'domain_type': S, 'status': S, 'tls_status': S,
    'environment_id': U, 'created_at': DT})
CONFIG = obj({'id': U, 'environment': U, 'created_by': U, 'version': I,
    'is_draft': B, 'mode': S, 'resource_plan': openapi.Schema(type='string', format='uuid', x_nullable=True),
    'runtime_type': S, 'build_method': S, 'branch': S, 'root_directory': S, 'dockerfile_path': S,
    'build_command': S, 'start_command': S, 'output_directory': S, 'internal_port': I,
    'healthcheck_path': S, 'auto_deploy': B, 'auto_deploy_branch': S, 'settings': openapi.Schema(type=openapi.TYPE_OBJECT),
    'created_at': DT})
SECRET = obj({'id': U, 'project': U, 'environment': openapi.Schema(type='string',format='uuid',x_nullable=True),
    'name': S, 'scope': S, 'is_active': B, 'version_ref': S, 'has_value': B,
    'created_at': DT, 'updated_at': DT})
INSTALL = obj({'id': I, 'account': S, 'repository_selection': S, 'permissions': ANY})
REPO = obj({'id': I, 'installation_id': I, 'name': S, 'full_name': S, 'private': B,
    'default_branch': S, 'html_url': S, 'description': NS})
BRANCH = obj({'name': S, 'protected': B, 'sha': S})
ORG = obj({'id': U, 'name': S, 'slug': S, 'role': S})
SMALL_ENV = obj({'id': U, 'name': S, 'slug': S, 'kind': S})
MODE = obj({'id': U, 'code': S, 'name': S, 'is_premium': B})
PLAN = obj({'id': U, 'code': S, 'name': S, 'cpu_millicores': I,
    'memory_mb': I, 'storage_mb': I, 'is_premium': B})
ENTITLEMENT = obj({'mode': S, 'resource_plan': U, 'status': S})

DATA = {
 'github_start': obj({'authorization_url': S, 'installation_url': S}),
 'github_status': obj({'connected': B, 'github_login': NS, 'installation_url': NS}),
 'github_disconnect': N,
 'github_installations': obj({'installations': arr(INSTALL)}),
 'github_repositories': obj({'repositories': arr(REPO), 'page': I, 'has_next': B}),
 'github_branches': obj({'repository_id': I, 'page': I, 'branches': arr(BRANCH)}),
 'github_webhook': obj({'accepted': B, 'recorded': I}),
 'organizations': obj({'organizations': arr(ORG)}),
 'project_list': obj({'projects': arr(PROJECT), 'page': I, 'page_size': I, 'total': I}),
 'project_create': obj({'project': PROJECT, 'environment_id': U, 'domain': NS}),
 'project_detail': obj({'project': PROJECT, 'environments': arr(SMALL_ENV)}),
 'project_update': obj({'project': PROJECT}),
 'project_archive': obj({'project': PROJECT}),
 'members_list': obj({'members': arr(obj({'id': U, 'email': S, 'role': S}))}),
 'members_add': obj({'member': obj({'id': U, 'email': S, 'role': S})}),
 'members_update': obj({'member': obj({'id': U, 'email': S, 'role': S})}),
 'members_remove': N,
 'environment_list': obj({'environments': arr(ENVIRONMENT)}),
 'environment_create': obj({'environment': ENVIRONMENT}),
 'environment_detail': obj({'environment': ENVIRONMENT}),
 'environment_update': obj({'environment': ENVIRONMENT}),
 'variables_list': obj({'variables': arr(VARIABLE)}),
 'variables_create': obj({'variable': VARIABLE}),
 'variable_update': obj({'variable': VARIABLE}),
 'variable_delete': N,
 'domains_list': obj({'domains': arr(DOMAIN)}),
 'domains_create': obj({'domain': DOMAIN}),
 'domain_reassign': obj({'domain': DOMAIN}),
 'config_list': obj({'configurations': arr(CONFIG)}),
 'config_create': obj({'configuration': CONFIG}),
 'config_detail': obj({'configuration': CONFIG}),
 'config_update': obj({'configuration': CONFIG}),
 'config_finalize': obj({'configuration': CONFIG}),
 'offer_choices': obj({'modes': arr(MODE), 'resource_plans': arr(PLAN),
                      'entitlement': openapi.Schema(type='object',properties=ENTITLEMENT.properties,x_nullable=True)}),
 'secret_list': obj({'secrets': arr(SECRET)}),
 'secret_create': obj({'secret': SECRET}),
 'secret_replace': obj({'secret': SECRET}),
 'secret_delete': N,
}

ERROR = obj({'success': B, 'message': S, 'data': N, 'errors': ANY})


def responses(key, success=200, *, public=False):
    result = {success: openapi.Response('Réponse JSON réelle.', obj({
        'success': B, 'message': S, 'data': DATA[key], 'errors': N}))}
    for status, text in [(400, 'Entrées invalides'),(404, 'Ressource absente'),
                         (409, 'Conflit / GitHub à reconnecter'),(429, 'Limite atteinte'),
                         (500, 'Erreur interne'),(503, 'Fournisseur externe indisponible')]:
        result[status] = openapi.Response(text, ERROR)
    if not public:
        result[401] = openapi.Response('JWT manquant ou invalide', ERROR)
        result[403] = openapi.Response('Accès refusé ou offre insuffisante', ERROR)
    return result
