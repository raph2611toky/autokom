"""Petites fonctions transversales communes aux API AUTOKOM."""
from django.utils.text import slugify
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
import os
import base64
import hashlib

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:
    Cipher = algorithms = modes = default_backend = None
    

class SecureTokenError(ValueError):
    """Erreur générique utilisée pour ne pas exposer le détail cryptographique."""

def normalize_email(value):
    return str(value or '').strip().lower()

def safe_slug(value):
    return slugify(str(value or ''))

def get_bearer_token(request):
    authorization = request.headers.get('Authorization', '')
    scheme, sep, token = authorization.partition(' ')
    return token.strip() if sep and scheme.lower() == 'bearer' else None

def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)

def _encryption_key() -> bytes:
    raw_key = os.getenv("TOKEN_ENCRYPTION_KEY") or os.getenv("AES_KEY") or settings.SECRET_KEY
    return hashlib.sha256(raw_key.encode("utf-8")).digest()

def encrypt_text(value: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_encryption_key()).encrypt(nonce, value.encode("utf-8"), None)
    return _urlsafe_b64encode(nonce + ciphertext)


def decrypt_text(value: str) -> str:
    try:
        raw = _urlsafe_b64decode(value)
        nonce, ciphertext = raw[:12], raw[12:]
        return AESGCM(_encryption_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception as exc:
        raise SecureTokenError("Jeton chiffré invalide.") from exc


def enc_dec(plaintext: str, type: str = "e") -> str:
    """Compatibilité avec l'ancien helper, désormais basé sur AES-GCM."""
    if type == "e":
        return encrypt_text(str(plaintext))
    if type == "d":
        return decrypt_text(plaintext)
    raise ValueError("Type de chiffrement inconnu.")