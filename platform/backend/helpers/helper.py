"""Petites fonctions transversales communes aux API AUTOKOM."""
from django.utils.text import slugify

def normalize_email(value):
    return str(value or '').strip().lower()

def safe_slug(value):
    return slugify(str(value or ''))

def get_bearer_token(request):
    authorization = request.headers.get('Authorization', '')
    scheme, sep, token = authorization.partition(' ')
    return token.strip() if sep and scheme.lower() == 'bearer' else None
