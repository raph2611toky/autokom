import logging
from django.db import IntegrityError
from django.http import Http404
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from rest_framework.exceptions import APIException, ValidationError
from helpers.responses import api_error

logger = logging.getLogger('autokom.api')


def _error_object(details):
    """Contrat JSON homogène : errors est un objet ou null, jamais une liste brute."""
    if details is None:
        return None
    if isinstance(details, dict):
        return details
    if isinstance(details, (list, tuple)):
        return {'non_field_errors': list(details)}
    return {'non_field_errors': [str(details)]}


def handle_error(exc, action):
    if isinstance(exc, DjangoValidationError):
        details = exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
        return api_error('Données invalides.', errors=_error_object(details), status_code=400)
    if isinstance(exc, ValidationError):
        return api_error('Données invalides.', errors=_error_object(exc.detail), status_code=400)
    if isinstance(exc, Http404):
        return api_error('Ressource introuvable.', status_code=404)
    if isinstance(exc, PermissionDenied):
        return api_error('Accès refusé.', status_code=403)
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
                'errors': _error_object({k:v for k,v in data.items() if k!='detail'} if isinstance(data, dict) else data)}
    if res.data['errors'] == {}:
        res.data['errors'] = None
    return res
