#!/usr/bin/env python3
"""Persist and reuse a deliberate, private-NIC IPv4 HTTPS bind policy.

Default remains the historical wildcard when no verified state is present.
Only the explicit repair utility writes the policy file.
"""
from __future__ import annotations
import ipaddress
import json
from pathlib import Path
import stat

STATE = Path('/etc/autokom/state/nginx-https-bind.json')

class BindPolicyError(RuntimeError):
    pass

def addresses(*, state_path=None):
    path = Path(state_path) if state_path is not None else STATE
    if not path.exists():
        if path.is_symlink():
            raise BindPolicyError('État HTTPS symbolique non autorisé')
        return ()
    if path.is_symlink():
        raise BindPolicyError('État HTTPS symbolique non autorisé')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 2048 or info.st_uid != 0 or info.st_mode & 0o077:
        raise BindPolicyError('État HTTPS non fiable : propriétaire/permissions')
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('schema') != 1 or state.get('port') != 443:
            raise ValueError('schema/port invalides')
        ips = state['addresses']
        if not isinstance(ips, list) or len(ips) != 2:
            raise ValueError('nombre d’adresses invalide')
        public, loopback = map(ipaddress.IPv4Address, ips)
        if loopback != ipaddress.IPv4Address('127.0.0.1'):
            raise ValueError('loopback invalide')
        if public.is_loopback or public.is_unspecified or public.is_multicast or public.is_link_local:
            raise ValueError('IP routée invalide')
        return (str(public), str(loopback))
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise BindPolicyError('État HTTPS invalide : réparation manuelle requise') from exc


def https_listens(*, indent='    ', flags='ssl', state_path=None):
    ips = addresses(state_path=state_path)
    if not ips:
        return f'{indent}listen 443 {flags};\n'
    return ''.join(f'{indent}listen {ip}:443 {flags};\n' for ip in ips)


def tls_detected(text):
    """Detect basic 443 TLS listeners, including IP-specific policy sockets."""
    import re
    return bool(re.search(r'(?m)^\s*listen\s+(?:(?:\d{1,3}\.){3}\d{1,3}:|\[::\]:)?443\b[^;]*\bssl\b[^;]*;', text))
