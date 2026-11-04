"""OpenAPI response envelopes mirroring actual JSON, secrets never represented."""
from drf_yasg import openapi

S = openapi.Schema(type=openapi.TYPE_STRING)
I = openapi.Schema(type=openapi.TYPE_INTEGER)
B = openapi.Schema(type=openapi.TYPE_BOOLEAN)
O = openapi.Schema(type=openapi.TYPE_OBJECT)
N = openapi.Schema(type=openapi.TYPE_OBJECT, x_nullable=True)


def envelope(properties, status=200):
    data = openapi.Schema(type=openapi.TYPE_OBJECT, properties=properties)
    result = openapi.Schema(type=openapi.TYPE_OBJECT, properties={
        'success': B, 'message': S, 'data': data, 'errors': N,
    }, required=['success', 'message','data','errors'])
    error = openapi.Schema(type=openapi.TYPE_OBJECT, properties={
        'success': B, 'message': S, 'data': N, 'errors': N,
    })
    return {status: openapi.Response('Succès.', result),
            400: openapi.Response('Validation impossible.', error),
            401: openapi.Response('Jeton client invalide.', error),
            403: openapi.Response('Permission / offre insuffisante.', error),
            404: openapi.Response('Objet absent.', error),
            409: openapi.Response('Conflit ou préparation manquante.', error),
            429: openapi.Response('Trop de requêtes.', error),
            503: openapi.Response('Runner indisponible.', error)}
