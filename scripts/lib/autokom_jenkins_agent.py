#!/usr/bin/env python3
"""AUTOKOM local Jenkins inbound agent provisioning.

No untrusted Groovy/script console, no passwords on the command line, no
Docker socket in the builder, and no reverse proxy dependency for WebSocket.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from xml.etree import ElementTree as ET

NODE_NAME = 'autokom-builder-01'  # never reuse the disclosed legacy node secret
LABEL = 'autokom-isolated'
AGENT_USER = 'autokom-builder'
AGENT_ROOT = Path('/var/lib/autokom-jenkins-agent-v2')
SECRET_FILE = AGENT_ROOT / '.agent-secret'
JAR_FILE = AGENT_ROOT / 'agent.jar'
UNIT = 'autokom-jenkins-agent.service'
UNIT_TARGET = Path('/etc/systemd/system') / UNIT
UNIT_SOURCE = Path(__file__).resolve().parents[2] / 'scripts/systemd' / UNIT

class AgentError(Exception):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def local_url(env):
    url = env.get('AUTOKOM_JENKINS_URL', '').rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed.port != 8080 or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise AgentError('Agent local : AUTOKOM_JENKINS_URL doit être http://127.0.0.1:8080.')
    return url


def backend_url():
    """Only public HTTPS URL is exported to build processes, never backend secrets."""
    env_path = Path('/etc/autokom/secrets/frontend.env')
    if not env_path.is_file():
        raise AgentError('frontend.env absent : URL backend interne impossible à vérifier.')
    values = {}
    for line in env_path.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    url = values.get('BACKEND_URL', '').rstrip('/')
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname
            or not re.fullmatch(r'[a-z0-9.-]{4,253}', parts.hostname)
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.query or parts.fragment or parts.path):
        raise AgentError('BACKEND_URL doit être une origine HTTPS sans chemin ni identifiants.')
    return url


def http(env, method, path, *, data=None, content_type=None, max_size=100_000):
    url = local_url(env)
    username = env.get('AUTOKOM_JENKINS_USER', '')
    api_token = env.get('AUTOKOM_JENKINS_API_TOKEN', '')
    if not username or not api_token:
        raise AgentError('Identifiants Jenkins manquants dans runtime.env.')
    if not path.startswith('/') or '//' in path or '..' in path:
        raise AgentError('Chemin API Jenkins non autorisé.')
    token = base64.b64encode(f'{username}:{api_token}'.encode()).decode('ascii')
    headers = {'Authorization': 'Basic ' + token}
    if content_type:
        headers['Content-Type'] = content_type
    request = Request(url + path, method=method, headers=headers, data=data)
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            payload = response.read(max_size + 1)
            if len(payload) > max_size:
                raise AgentError('Réponse Jenkins anormalement volumineuse.')
            return response.status, payload
    except HTTPError as exc:
        # Never forward HTML errors: they may contain cookies, tokens, or URLs.
        return exc.code, b''
    except (URLError, TimeoutError, OSError) as exc:
        raise AgentError('Jenkins local inaccessible.') from exc


def expected_node_form():
    # Stapler submitted form, not a Groovy script console command.
    return {
        'name': NODE_NAME,
        'nodeDescription': 'AUTOKOM managed local unprivileged builder',
        'numExecutors': 2,
        'remoteFS': str(AGENT_ROOT),
        'labelString': LABEL,
        'mode': 'EXCLUSIVE',
        'type': 'hudson.slaves.DumbSlave',
        'retentionStrategy': {'stapler-class': 'hudson.slaves.RetentionStrategy$Always'},
        'launcher': {'stapler-class': 'hudson.slaves.JNLPLauncher'},
        'nodeProperties': {'stapler-class-bag': 'true'},
    }


def validate_node(xml, *, allow_legacy=False):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise AgentError('Configuration du nœud Jenkins illisible.') from exc
    actual = {
        'name': root.findtext('name'),
        'remoteFS': root.findtext('remoteFS'),
        'numExecutors': root.findtext('numExecutors'),
        'label': root.findtext('label'),
        'mode': root.findtext('mode'),
    }
    # Jenkins XML uses <label> or <labelString> depending on serializer.
    if actual['label'] is None:
        actual['label'] = root.findtext('labelString')
    launcher = root.find('launcher')
    launcher_class = '' if launcher is None else launcher.attrib.get('class', '')
    if (actual['name'] not in (None, NODE_NAME) or actual['remoteFS'] != str(AGENT_ROOT)
            or actual['numExecutors'] not in (('1', '2') if allow_legacy else ('2',)) or actual['label'] != LABEL
            or actual['mode'] != 'EXCLUSIVE'
            or 'JNLPLauncher' not in launcher_class):
        raise AgentError('Un nœud existe déjà sous ce nom avec une configuration différente. Refus de l’écraser.')


def ensure_node(env):
    from urllib.parse import quote
    endpoint = '/computer/' + quote(NODE_NAME, safe='') + '/config.xml'
    status, data = http(env, 'GET', endpoint)
    if status == 200:
        # Migrate ONLY the existing, recognized one-executor isolated node.
        # Do not overwrite an administrator-controlled or unfamiliar Jenkins node.
        validate_node(data, allow_legacy=True)
        root = ET.fromstring(data)
        # Reconfiguring or restarting a busy Jenkins node risks disrupting a
        # customer build, even when its executor count is already compliant.
        # The caller should finish active builds before applying the upgrade.
        check = '/computer/' + quote(NODE_NAME, safe='') + '/api/json?tree=executors[currentExecutable],oneOffExecutors[currentExecutable]'
        state_status, state = http(env, 'GET', check, max_size=32_768)
        if state_status != 200:
            raise AgentError('Impossible de confirmer que l’agent Jenkins est inactif.')
        try:
            details = json.loads(state)
            lists = [details['executors'], details.get('oneOffExecutors', [])]
            if not all(isinstance(items, list) for items in lists):
                raise ValueError('Invalid executors')
            if any(not isinstance(item, dict) or item.get('currentExecutable') is not None
                   for items in lists for item in items):
                raise AgentError('Agent occupé : terminer les builds avant migration à deux exécuteurs.')
        except (ValueError, KeyError, TypeError) as exc:
            raise AgentError('Impossible de confirmer les exécuteurs Jenkins disponibles.') from exc
        if root.findtext('numExecutors') == '2':
            return False
        root.find('numExecutors').text = '2'
        xml = ET.tostring(root, encoding='utf-8')
        changed, _ = http(env, 'POST', endpoint, data=xml, content_type='application/xml')
        if changed not in (200, 201, 204):
            raise AgentError(f'Migration contrôlée des exécuteurs refusée (HTTP {changed}).')
        confirmed, final = http(env, 'GET', endpoint)
        if confirmed != 200:
            raise AgentError('Migration Jenkins non confirmée par l’API.')
        validate_node(final)
        return True
    if status != 404:
        raise AgentError(f'Lecture du nœud refusée par Jenkins (HTTP {status}).')
    # Jenkins installations expose one of these two descriptor IDs.
    # Retry ONLY on HTTP 400, after confirming the node was not created.
    for descriptor in ('hudson.slaves.DumbSlave', 'hudson.slaves.DumbSlave$DescriptorImpl'):
        node = expected_node_form()
        node['type'] = descriptor
        request_data = urlencode({'name': NODE_NAME, 'type': descriptor,
                                  'json': json.dumps(node, separators=(',', ':'))}).encode()
        status, _ = http(env, 'POST', '/computer/doCreateItem', data=request_data,
                         content_type='application/x-www-form-urlencoded')
        if status in (200, 201, 302):
            status, data = http(env, 'GET', endpoint)
            if status != 200:
                raise AgentError('Création du nœud non confirmée par l’API Jenkins.')
            validate_node(data)
            return True
        if status == 400:
            probe_status, probe_data = http(env, 'GET', endpoint)
            if probe_status == 200:
                validate_node(probe_data)
                return True
            if probe_status == 404:
                continue
        raise AgentError(f'Création du nœud Jenkins refusée (HTTP {status}). Agent non installé.')
    raise AgentError('Format de création des nœuds non accepté par Jenkins.')


def get_secret(env):
    path = '/computer/' + quote(NODE_NAME, safe='') + '/jenkins-agent.jnlp'
    status, xml = http(env, 'GET', path, max_size=40_000)
    if status != 200:
        raise AgentError(f'Secret du nœud non accessible (HTTP {status}). Droit Agent/Connect requis.')
    try:
        root = ET.fromstring(xml)
        values = [e.text or '' for e in root.iter() if e.tag.split('}')[-1] == 'argument']
    except ET.ParseError as exc:
        raise AgentError('Réponse d’enrôlement Jenkins invalide.') from exc
    if not values or not re.fullmatch(r'[0-9a-fA-F]{64}', values[0]):
        raise AgentError('Jenkins ne fournit pas un secret d’enrôlement valide.')
    return values[0].encode('ascii')


def java_agent_jar(env):
    status, content = http(env, 'GET', '/jnlpJars/agent.jar', max_size=35 * 1024 * 1024)
    if status != 200 or len(content) < 128 * 1024 or not content.startswith(b'PK\x03\x04'):
        raise AgentError('agent.jar invalide ou indisponible sur Jenkins local.')
    return content


def ensure_private_dir():
    if AGENT_ROOT.is_symlink():
        raise AgentError('Répertoire agent : lien symbolique interdit.')
    AGENT_ROOT.mkdir(parents=True, exist_ok=True)
    builder = pwd.getpwnam(AGENT_USER)
    os.chown(AGENT_ROOT, builder.pw_uid, builder.pw_gid)
    os.chmod(AGENT_ROOT, 0o700)
    return builder


def write_private_file(path, data, user, *, mode=0o400, allow_update=False):
    if path.is_symlink():
        raise AgentError('Fichier de l’agent non conforme (symlink).')
    if path.exists():
        flags_read = os.O_RDONLY | (getattr(os, 'O_NOFOLLOW', 0))
        fd_old = os.open(path, flags_read)
        try:
            info = os.fstat(fd_old)
            if not stat.S_ISREG(info.st_mode):
                raise AgentError('Fichier agent : type non autorisé.')
            with os.fdopen(fd_old, 'rb', closefd=False) as stream:
                old = stream.read(35 * 1024 * 1024 + 1)
        finally:
            os.close(fd_old)
        if hmac.compare_digest(old, data):
            os.chown(path, user.pw_uid, user.pw_gid, follow_symlinks=False)
            os.chmod(path, mode, follow_symlinks=False)
            return False
        if not allow_update:
            raise AgentError('Secret du nœud modifié : refus d’écraser. Rotation contrôlée requise.')
    tmp = path.with_name('.autokom-tmp-' + str(os.getpid()) + '-' + path.name)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, mode)
    try:
        os.fchown(fd, user.pw_uid, user.pw_gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def provision_system_dependencies(*, apply):
    """Ansible owns Java/Git/ACL/UID setup; no manual apt/chmod."""
    from autokom_runtime_admin import ROOT, run_live
    playbook = ROOT / 'ansible/playbooks/jenkins-agent.yml'
    args = ['ansible-playbook', '-i', str(ROOT / 'ansible/inventories/local/hosts.ini')]
    env = os.environ.copy()
    env['ANSIBLE_CONFIG'] = str(ROOT / 'ansible/ansible.cfg')
    if not apply:
        args.append('--check')
    args.append(str(playbook))
    run_live(args, env=env, label='Préparation isolée de l’agent Jenkins', timeout=900)


def verify_jenkins_identity(env):
    status, raw = http(env, 'GET', '/whoAmI/api/json', max_size=16_384)
    if status in (401, 403):
        raise AgentError('Jenkins actif, mais compte/API token refusé (HTTP '+str(status)+'). '
                         'Vérifier AUTOKOM_JENKINS_USER et le token dans runtime.env ; '
                         'aucun remplacement automatique des secrets.')
    if status != 200:
        raise AgentError('API d’identité Jenkins indisponible (HTTP '+str(status)+'). '
                         'Vérifier le contrôleur Jenkins local et son URL.')
    try:
        identity = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise AgentError('API Jenkins : réponse d’identité invalide.') from exc
    if not isinstance(identity, dict) or identity.get('authenticated') is not True:
        raise AgentError('API Jenkins : session anonyme ou authentification non confirmée.')
    return True


def preflight(env):
    import shutil
    verify_jenkins_identity(env)
    if not shutil.which('java') or not Path('/usr/bin/java').exists():
        raise AgentError('Java absent : relancer le provisionnement Ansible de l’agent.')
    if not shutil.which('git'):
        raise AgentError('Git absent : relancer le provisionnement Ansible de l’agent.')
    return True


def write_unit(api_base):
    # Fixed, audited service unit: never interpolate shell/secret/URL from customers.
    source = UNIT_SOURCE.read_text(encoding='utf-8')
    if '__AUTOKOM_BACKEND_URL__' not in source:
        raise AgentError('Unité systemd AUTOKOM incomplète.')
    unit = source.replace('__AUTOKOM_BACKEND_URL__', api_base)
    if UNIT_TARGET.is_symlink():
        raise AgentError('Unité systemd : lien symbolique inattendu.')
    tmp = UNIT_TARGET.with_name('.autokom-agent-' + str(os.getpid()) + '.service')
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(unit)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, UNIT_TARGET)
        os.chmod(UNIT_TARGET, 0o644)
    finally:
        tmp.unlink(missing_ok=True)


def systemctl(*args):
    p = subprocess.run(['systemctl', *args], capture_output=True, timeout=30, check=False)
    if p.returncode:
        raise AgentError('Échec systemd de l’agent. Consulter journalctl -u ' + UNIT)


def exact_agent_online(env):
    path = '/computer/api/json?tree=computer%5BdisplayName,offline,temporarilyOffline,numExecutors,assignedLabels%5Bname%5D%5D'
    status, raw = http(env, 'GET', path)
    if status != 200:
        raise AgentError('État de l’agent non accessible via Jenkins.')
    try:
        computers = json.loads(raw)['computer']
        node = next((x for x in computers if x.get('displayName') == NODE_NAME), None)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AgentError('Réponse de supervision de l’agent invalide.')
    if not node:
        return False
    labels = {x.get('name') for x in node.get('assignedLabels', []) if isinstance(x, dict)}
    return (LABEL in labels and node.get('offline') is False
            and node.get('temporarilyOffline') is not True
            and type(node.get('numExecutors')) is int and node['numExecutors'] == 2)


def await_online(env, *, timeout=45):
    import time
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if exact_agent_online(env):
            return True
        print(f'  › Agent en connexion · vérification {attempt} (WebSocket local)...', flush=True)
        time.sleep(min(3, max(0, deadline - time.monotonic())))
    raise AgentError('Agent Java démarré mais non connecté. Vérifier journalctl -u ' + UNIT)


def provision(env, *, apply):
    api_base = backend_url()
    local_url(env)
    print('  ◈ Agent AUTOKOM : nœud local isolé, sans proxy public et sans accès Docker')
    print('  › Nœud prévu : ' + NODE_NAME + ' · label : ' + LABEL)
    print('  › URL de liaison : http://127.0.0.1:8080/ (loopback uniquement)')
    verify_jenkins_identity(env)
    print('  ✓ API Jenkins : compte de service authentifié sur loopback')
    if not apply:
        print('  ✓ Prévisualisation : agent Jenkins, BuildKit rootless et services systemd prévus.')
        return 0
    # Caller provides the confirmation before running this mutable routine.
    provision_system_dependencies(apply=True)
    preflight(env)
    user = ensure_private_dir()
    created = ensure_node(env)
    print('  ✓ Nœud Jenkins : ' + ('créé' if created else 'déjà conforme'))
    secret = get_secret(env)
    write_private_file(SECRET_FILE, secret + b'\n', user, mode=0o400)
    print('  ✓ Secret d’enrôlement récupéré et protégé (valeur non affichée).')
    jar = java_agent_jar(env)
    write_private_file(JAR_FILE, jar, user, mode=0o400, allow_update=True)
    print('  ✓ Agent Java obtenu depuis Jenkins local (version du contrôleur).')
    write_unit(api_base)
    systemctl('daemon-reload')
    systemctl('enable', '--now', UNIT)
    systemctl('restart', UNIT)
    print('  ✓ Service systemd : ' + UNIT)
    await_online(env)
    from autokom_buildkit_admin import healthcheck, BuildkitError
    try:
        healthcheck()
    except BuildkitError as exc:
        raise AgentError(str(exc)) from exc
    print('  ✓ BuildKit rootless prêt : socket privé et worker opérationnel.')
    print('  ✓ Agent isolé connecté à Jenkins (API vérifiée).')
    print('  › Ancien nœud autokom-isolated : révoquer s’il existe encore (secret déjà exposé).')
    return 0
