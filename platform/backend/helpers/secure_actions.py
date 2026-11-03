"""Jetons d'action à usage unique : JWT signé puis chiffré avec AES-256-GCM.

La base conserve uniquement le SHA-256 du jeton final. Les clés sont séparées
par domaine d'usage; ni e-mail ni UUID utilisateur ne figurent en clair dans l'URL.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import timedelta

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.utils import timezone


class SecureActionTokenError(ValueError):
    """Erreur stable sans divulgation des données cryptographiques."""


def _base_key() -> bytes:
    source = (getattr(settings, 'TOKEN_ENCRYPTION_KEY', '') or
              os.getenv('TOKEN_ENCRYPTION_KEY') or os.getenv('AES_KEY') or settings.SECRET_KEY)
    if not source or len(source) < 24:
        raise SecureActionTokenError('Clé cryptographique non configurée.')
    return hashlib.sha256(b'autokom-action-aead-v1|' + source.encode('utf-8')).digest()


def _signing_key() -> bytes:
    return hashlib.sha256(b'autokom-action-sign-v1|' + settings.CLIENT_JWT_SIGNING_KEY.encode('utf-8')).digest()


def _base64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _base64_decode(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))


def enc_dec(value: str, type: str = 'e') -> str:
    """API proche du helper fourni : 'e' chiffre, 'd' déchiffre."""
    if type == 'e':
        nonce = os.urandom(12)
        encrypted = AESGCM(_base_key()).encrypt(nonce, str(value).encode('utf-8'), b'autokom-action-v1')
        return _base64_encode(nonce + encrypted)
    if type != 'd':
        raise SecureActionTokenError('Opération invalide.')
    try:
        raw = _base64_decode(value)
        if len(raw) < 29:
            raise ValueError('too short')
        return AESGCM(_base_key()).decrypt(raw[:12], raw[12:], b'autokom-action-v1').decode('utf-8')
    except Exception as exc:
        raise SecureActionTokenError('Jeton invalide ou expiré.') from exc


def generate_secure_action_token(user, *, purpose: str, lifetime: timedelta) -> str:
    now = timezone.now()
    claims = {
        'sub': str(user.pk), 'purpose': purpose, 'jti': secrets.token_urlsafe(32),
        'ver': int(user.token_version), 'iat': now, 'nbf': now, 'exp': now + lifetime,
        'iss': settings.CLIENT_JWT_ISSUER, 'aud': 'autokom-client-actions',
    }
    signed = jwt.encode(claims, _signing_key(), algorithm='HS256')
    return 'ar1.' + enc_dec(signed, 'e')


def validate_secure_action_token(raw: str, *, user, expected_purpose: str) -> dict:
    try:
        if not raw.startswith('ar1.') or len(raw) > 2048:
            raise ValueError('format')
        signed = enc_dec(raw[4:], 'd')
        claims = jwt.decode(
            signed, _signing_key(), algorithms=['HS256'],
            audience='autokom-client-actions', issuer=settings.CLIENT_JWT_ISSUER,
            options={'require': ['sub', 'purpose', 'jti', 'ver', 'iat', 'nbf', 'exp', 'iss', 'aud']},
        )
        if (claims['sub'] != str(user.pk) or claims['purpose'] != expected_purpose or
                int(claims['ver']) != user.token_version):
            raise ValueError('wrong claim')
        return claims
    except Exception as exc:
        raise SecureActionTokenError('Jeton invalide ou expiré.') from exc
