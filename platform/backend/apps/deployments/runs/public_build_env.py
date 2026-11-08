"""Build-time public variables; never allow secrets into a browser bundle."""
import re

PUBLIC_PREFIXES = {
    'vite': ('VITE_',),
    'npm_static': ('VITE_', 'REACT_APP_', 'VUE_APP_'),
    'next_export': ('NEXT_PUBLIC_',),
    'next_server': ('NEXT_PUBLIC_',),
    'html': (),
}
_KEY = re.compile(r'^[A-Z][A-Z0-9_]{0,99}$')
_SECRET = re.compile(r'SECRET|PASSWORD|TOKEN|PRIVATE|CREDENTIAL|API_KEY|ACCESS_KEY|SESSION|AUTH_KEY', re.I)


def public_frontend_variables(items, profile):
    prefixes = PUBLIC_PREFIXES.get(profile)
    if prefixes is None:
        raise ValueError('Recette frontend non autorisée.')
    result = {}
    for key, value, secret_id in items:
        if not isinstance(key, str) or not any(key.startswith(p) for p in ('VITE_', 'REACT_APP_', 'VUE_APP_', 'NEXT_PUBLIC_')):
            continue
        if not any(key.startswith(p) and len(key) > len(p) for p in prefixes):
            continue  # Old framework values remain stored but are never injected.
        if secret_id is not None or not _KEY.fullmatch(key) or _SECRET.search(key):
            raise ValueError('Variable frontend publique invalide ou sensible.')
        if not isinstance(value, str) or len(value) > 1024 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError('Valeur de variable frontend publique invalide.')
        if len(result) >= 40:
            raise ValueError('Trop de variables publiques.')
        result[key] = value
    return dict(sorted(result.items()))


def public_vite_variables(items):
    """Compatibility for the previous Vite-only API and existing tests."""
    return public_frontend_variables(items, 'vite')
