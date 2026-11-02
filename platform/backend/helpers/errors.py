import logging
from django.db import IntegrityError
from rest_framework.exceptions import APIException, ValidationError
from helpers.responses import api_error

logger = logging.getLogger('autokom.api')

def handle_error(exc, action):
    if isinstance(exc, ValidationError):
        return api_error('Données invalides.', errors=exc.detail, status_code=400)
    if isinstance(exc, APIException):
        return api_error(str(exc.detail), status_code=exc.status_code)
    if isinstance(exc, IntegrityError):
        return api_error('Conflit de données ou ressource déjà existante.', status_code=409)
    logger.exception('Échec inattendu de %s', action, exc_info=(type(exc), exc, exc.__traceback__))
    return api_error('Erreur interne du serveur.', status_code=500)

def global_api_exception_handler(exc, context):
    from rest_framework.views import exception_handler
    res = exception_handler(exc, context)
    if res is None:
        return None
    data = res.data
    detail = str(data.get('detail', 'Requête refusée.')) if isinstance(data, dict) else 'Requête refusée.'
    res.data = {'success': False, 'message': detail, 'data': None,
        'errors': data if isinstance(data, dict) and 'detail' not in data else None}
    return res
