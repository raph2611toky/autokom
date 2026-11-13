"""Manage one trusted Jenkins credential, exclusively from the root AUTOKOM CLI.

Never accept customer input, leak the key to subprocess arguments, or overwrite
unknown Jenkins credentials. A Jenkins API token with Credentials/Create is required.
"""
from __future__ import annotations

import hashlib
import json
import os
from xml.etree import ElementTree as ET

CREDENTIAL_ID = 'autokom-runner-key'
PATH = '/credentials/store/system/domain/_/credential/' + CREDENTIAL_ID + '/api/json?tree=id,typeName,description,_class'
CREATE = '/credentials/store/system/domain/_/createCredentials'


class CredentialSetupError(Exception):
    pass


def fingerprint(value):
    """Fingerprint a high-entropy secret for configuration drift detection."""
    if not isinstance(value, str) or not 32 <= len(value) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise CredentialSetupError('AUTOKOM_RUNNER_SHARED_KEY invalide (32+ caractères ASCII sans espaces).')
    return hashlib.sha256(value.encode('ascii')).hexdigest()[:20]


def expected_description(env):
    return 'AUTOKOM managed runner v1 sha256:' + fingerprint(env.get('AUTOKOM_RUNNER_SHARED_KEY', ''))


def credential_xml(env, credential_id=CREDENTIAL_ID):
    root = ET.Element('org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl')
    ET.SubElement(root, 'scope').text = 'GLOBAL'
    ET.SubElement(root, 'id').text = credential_id
    ET.SubElement(root, 'secret').text = env['AUTOKOM_RUNNER_SHARED_KEY']
    ET.SubElement(root, 'description').text = expected_description(env)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def status(env, request, credential_id=CREDENTIAL_ID):
    """Read-only. Return absent/managed; fail closed on unknown/external IDs."""
    if credential_id not in (CREDENTIAL_ID, 'autokom-preprod-runner-key'):
        raise CredentialSetupError('Identifiant Jenkins hors périmètre.')
    description = expected_description(env)
    path = PATH.replace(CREDENTIAL_ID, credential_id)
    code, payload = request(env, 'GET', path, content_type='application/json')
    if code == 404:
        return 'absent'
    if code != 200:
        raise CredentialSetupError('Jenkins refuse la consultation des identifiants (Credentials/View requis).')
    try:
        info = json.loads(payload)
    except (ValueError, TypeError):
        raise CredentialSetupError('Réponse Jenkins Credentials illisible.') from None
    if info.get('id') != credential_id:
        raise CredentialSetupError('Référence Jenkins Credentials inattendue.')
    type_name = str(info.get('typeName') or '').lower()
    cls = str(info.get('_class') or '').lower()
    if not ('secret text' in type_name or 'stringcredentialsimpl' in cls):
        raise CredentialSetupError('Credential existant de type différent : ne pas écraser.')
    if info.get('description') != description:
        raise CredentialSetupError('Credential préexistant ou clé modifiée : rotation contrôlée requise; aucune modification.')
    return 'managed'


def provision(env, request, *, apply=False, credential_id=CREDENTIAL_ID):
    """Provision exactly one Secret text through the documented Jenkins XML API."""
    existing = status(env, request, credential_id)
    if existing == 'managed':
        return 'managed'
    if not apply:
        return 'absent'
    code, _ = request(env, 'POST', CREATE, credential_xml(env, credential_id), 'application/xml')
    if code not in (200, 201, 202, 302):
        raise CredentialSetupError('Création Jenkins refusée ; vérifier Credentials/Create et le plugin Plain Credentials.')
    if status(env, request, credential_id) != 'managed':
        raise CredentialSetupError('Identifiant créé mais non vérifiable ; ne pas lancer de publication.')
    return 'created'
