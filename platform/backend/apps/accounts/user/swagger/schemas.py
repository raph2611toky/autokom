"""Exact public JSON envelope returned by helpers.responses.*.

OpenAPI 2 limits nullable union schemas; `x-nullable` describes JSON null.
Every endpoint has its own typed `data` schema and shares error structure.
"""
from drf_yasg import openapi


def scalar(kind, *, required=False, nullable=False, description=''):
    opts = {'type': kind}
    if nullable:
        opts['x_nullable'] = True
    if description:
        opts['description'] = description
    return openapi.Schema(**opts)


def obj(properties, required=None, *, nullable=False, description=''):
    opts = {
        'type': openapi.TYPE_OBJECT,
        'properties': properties,
        'required': required if required is not None else list(properties),
    }
    if nullable:
        opts['x_nullable'] = True
    if description:
        opts['description'] = description
    return openapi.Schema(**opts)


def arr(item):
    return openapi.Schema(type=openapi.TYPE_ARRAY, items=item)


NULL = openapi.Schema(type=openapi.TYPE_OBJECT, x_nullable=True, description='Toujours null.')
ANY_ERROR = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    x_nullable=True,
    description='Objet ou tableau de validation, ou null lorsque sans détails.',
)
TEXT = scalar(openapi.TYPE_STRING)
BOOLEAN = scalar(openapi.TYPE_BOOLEAN)
UUID = openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_UUID)
TIMESTAMP = openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME, x_nullable=True)

USER = obj({
    'id': UUID, 'email': TEXT, 'full_name': TEXT,
    'avatar': openapi.Schema(type=openapi.TYPE_STRING, format='uri', x_nullable=True),
    'is_active': BOOLEAN, 'is_email_verified': BOOLEAN,
    'email_verified_at': TIMESTAMP,
    'created_at': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
    'last_login_at': TIMESTAMP,
})
TOKEN_PAIR = obj({
    'access': TEXT, 'refresh': TEXT, 'token_type': TEXT,
    'expires_in': scalar(openapi.TYPE_INTEGER), 'session_id': UUID,
})
SESSION = obj({
    'id': UUID,
    'created_at': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
    'expires_at': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
    'revoked_at': TIMESTAMP, 'last_seen_at': TIMESTAMP,
    'ip_address': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
    'user_agent': TEXT,
})
ORG_MEMBERSHIP = obj({'id': UUID, 'name': TEXT, 'slug': TEXT, 'role': TEXT})
PROJECT_MEMBERSHIP = obj({'id': UUID, 'name': TEXT, 'role': TEXT})

RESPONSE_DATA = {
    'register': obj({'user': USER, 'verification_email_sent': BOOLEAN}),
    'github_start': obj({'authorization_url': TEXT, 'browser_binding': TEXT}),
    'github_exchange': obj({'tokens': TOKEN_PAIR, 'user': USER}),
    'email_verify': NULL,
    'email_resend': NULL,
    'login': obj({'tokens': TOKEN_PAIR, 'user': USER}),
    'refresh': obj({'tokens': TOKEN_PAIR}),
    'logout': NULL,
    'logout_all': NULL,
    'forgot_password': NULL,
    'reset_password': NULL,
    'change_password': NULL,
    'me_get': obj({'user': USER}),
    'me_patch': obj({'user': USER}),
    'me_avatar': obj({'user': USER}),
    'sessions_list': obj({'sessions': arr(SESSION)}),
    'sessions_revoke': NULL,
    'permissions_get': obj({'organizations': arr(ORG_MEMBERSHIP), 'projects': arr(PROJECT_MEMBERSHIP)}),
}


def response_schema(key):
    return obj({
        'success': BOOLEAN,
        'message': TEXT,
        'data': RESPONSE_DATA[key],
        'errors': ANY_ERROR,
    })


ERROR = obj({'success': BOOLEAN, 'message': TEXT, 'data': NULL, 'errors': ANY_ERROR})


def response_map(key, *, success_status=200, public=False, include_not_found=False):
    from drf_yasg import openapi
    result = {
        success_status: openapi.Response('Structure JSON réellement retournée.', response_schema(key)),
        400: openapi.Response('Paramètres invalides.', ERROR),
        429: openapi.Response('Limite de requêtes dépassée.', ERROR),
        500: openapi.Response('Erreur interne.', ERROR),
    }
    if not public:
        result.update({
            401: openapi.Response('JWT requis ou invalide.', ERROR),
            403: openapi.Response('Permission refusée.', ERROR),
        })
    if include_not_found:
        result[404] = openapi.Response('Ressource introuvable.', ERROR)
    if key in ('login', 'reset_password', 'change_password', 'logout'):
        result[401] = openapi.Response('Identifiants invalides.', ERROR)
        result[403] = openapi.Response('Accès interdit.', ERROR)
    if key == 'github_start':
        result[503] = openapi.Response('GitHub indisponible ou non configuré.', ERROR)
    if key == 'register':
        result[409] = openapi.Response('Conflit en base.', ERROR)
    return result
