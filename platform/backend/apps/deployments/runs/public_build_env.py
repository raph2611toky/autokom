"""Validate public Vite build-time values. Never forward SecretReference values.

VITE_* values are embedded in the generated frontend JS. They are not secrets.
"""
import re

_VITE_KEY = re.compile(r'^VITE_[A-Z][A-Z0-9_]{0,79}$')
_SECRET_WORD = re.compile(r'(?:SECRET|PASSWORD|TOKEN|PRIVATE|CREDENTIAL|API_KEY|ACCESS_KEY|SESSION)', re.I)


def public_vite_variables(items):
    """Convert (key,value,secret_id) tuples into allowlisted, public build args.

    Invalid pre-existing values fail closed, avoiding unintentional secret exposure.
    """
    result = {}
    for key, value, secret_id in items:
        if not isinstance(key, str) or not key.startswith('VITE_'):
            continue
        if secret_id is not None or not _VITE_KEY.fullmatch(key) or _SECRET_WORD.search(key):
            raise ValueError('Variable Vite publique non autorisée.')
        if not isinstance(value, str) or len(value) > 1024 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('Valeur de variable Vite publique non autorisée.')
        if len(result) >= 40:
            raise ValueError('Trop de variables Vite publiques.')
        result[key] = value
    return dict(sorted(result.items()))
