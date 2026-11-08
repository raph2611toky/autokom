#!/usr/bin/env python3
"""Rotate the Jenkins API token used by AUTOKOM without printing or leaking it.

The token must come from the Jenkins account's Security > API Token page.
Never accept it as a CLI argument, or create/change Jenkins users automatically.
"""
from __future__ import annotations

import getpass
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from autokom_env_manager import backup, read_env, write_env
from autokom_jenkins_agent import http as jenkins_http, AgentError
from autokom_control_recovery import inspect as control_inspect
from cli_colors import pretty_print as say

ENV_PATH = Path('/etc/autokom/secrets/runtime.env')
BROKER_UNIT = 'autokom-platform-broker.service'


class JenkinsTokenError(RuntimeError):
    pass


def _configured():
    if ENV_PATH.is_symlink() or not ENV_PATH.is_file():
        raise JenkinsTokenError('runtime.env absent ou non autorisé. Ne pas remplacer les autres secrets.')
    metadata = ENV_PATH.stat()
    if (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600
            or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
        raise JenkinsTokenError('runtime.env doit être un fichier ordinaire root:0600 non partagé.')
    try:
        data = read_env(ENV_PATH)
    except (OSError, ValueError) as exc:
        raise JenkinsTokenError('runtime.env invalide ou illisible.') from exc
    if not data.get('AUTOKOM_JENKINS_USER') or not data.get('AUTOKOM_JENKINS_URL'):
        raise JenkinsTokenError('Utilisateur ou adresse Jenkins manquant dans runtime.env.')
    return data


def _identity(env):
    """Authenticated identity, never the public /login HTML page."""
    try:
        status, response = jenkins_http(env, 'GET', '/whoAmI/api/json', max_size=16_384)
    except AgentError as exc:
        raise JenkinsTokenError('Jenkins local inaccessible ou URL Jenkins incorrecte.') from exc
    if status in (401, 403):
        raise JenkinsTokenError('Compte ou token Jenkins refusé (HTTP '+str(status)+').')
    if status != 200:
        raise JenkinsTokenError('Contrôle identité Jenkins non valide (HTTP '+str(status)+').')
    try:
        identity = json.loads(response)
    except (TypeError, ValueError) as exc:
        raise JenkinsTokenError('Jenkins : réponse API non JSON.') from exc
    if not isinstance(identity, dict) or identity.get('authenticated') is not True:
        raise JenkinsTokenError('Jenkins : connexion anonyme, token non validé.')
    return identity


def _job_access(env):
    from urllib.parse import quote
    name = env.get('AUTOKOM_JENKINS_JOB', 'autokom-client-deploy')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name):
        raise JenkinsTokenError('Nom du job Jenkins non valide.')
    try:
        status, _ = jenkins_http(env, 'GET', '/job/'+quote(name, safe='')+'/api/json', max_size=16_384)
    except AgentError as exc:
        raise JenkinsTokenError('Impossible de vérifier le job Jenkins.') from exc
    if status in (401,403):
        raise JenkinsTokenError('Identité Jenkins authentifiée, mais accès au job refusé (HTTP '+str(status)+').')
    if status not in (200,404):
        raise JenkinsTokenError('Jenkins : contrôle du job anormal (HTTP '+str(status)+').')
    return status == 200


def _systemctl(*args):
    try:
        result = subprocess.run(['systemctl', *args], check=False, capture_output=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JenkinsTokenError('Impossible de contrôler le service broker systemd.') from exc
    if result.returncode:
        raise JenkinsTokenError('Redémarrage systemd du broker refusé (consulter journalctl -u '+BROKER_UNIT+').')


def _broker_ready():
    try:
        state = control_inspect()
    except Exception:
        return False
    return state.broker == 'active' and state.socket == 'ok'


# Run from the same unprivileged identity as Django, not root. No token is
# passed in argv, environment, output or through the Unix socket.
_BROKER_IDENTITY_PROBE = r'''import json,socket,sys
try:
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
  sock.settimeout(6)
  sock.connect('/run/autokom/platform-control.sock')
  sock.sendall(b'{"action":"jenkins.identity","params":{}}\n')
  line=b''
  while not line.endswith(b'\n') and len(line)<8192:
   part=sock.recv(8192)
   if not part:break
   line+=part
  data=json.loads(line)
  sys.exit(0 if data.get('ok') and data.get('result')=={'authenticated':True} else 7)
except Exception:sys.exit(8)
'''


def _broker_identity():
    try:
        result=subprocess.run(['runuser','-u','autokom','--','/usr/bin/python3','-c',
                               _BROKER_IDENTITY_PROBE], capture_output=True, timeout=10)
    except (OSError,subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _prompt_new_token():
    if not os.isatty(0) or not os.isatty(1):
        raise JenkinsTokenError('Terminal interactif requis pour saisir le token sans affichage.')
    try:
        first = getpass.getpass('  🔒 Nouveau token API Jenkins (masqué) : ')
        second = getpass.getpass('  🔒 Confirmer le token (masqué) : ')
    except (KeyboardInterrupt, EOFError) as exc:
        raise JenkinsTokenError('Rotation du token annulée.') from exc
    if (not first or len(first) < 20 or len(first) > 256
            or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in first)):
        raise JenkinsTokenError('Token API Jenkins invalide (au moins 20 caractères ASCII sans espace).')
    if not hmac.compare_digest(first, second):
        raise JenkinsTokenError('Les deux tokens saisis ne correspondent pas.')
    return first


def check():
    data = _configured()
    say('  ◈ AUTOKOM · JENKINS · IDENTIFIANTS DE SERVICE')
    say('  › Compte : '+data['AUTOKOM_JENKINS_USER'])
    say('  › Fichier : '+str(ENV_PATH)+' (valeurs masquées)')
    if not data.get('AUTOKOM_JENKINS_API_TOKEN'):
        say('  ✗ Aucun token Jenkins enregistré. Utiliser --apply.')
        return 1
    try:
        _identity(data)
        has_job = _job_access(data)
    except JenkinsTokenError as exc:
        say('  ✗ '+str(exc))
        say('  › Réparation : créer un token sous Jenkins > Compte > Sécurité, puis :')
        say('    sudo autokom runtime jenkins-token --apply')
        return 1
    say('  ✓ Identité Jenkins validée (API authentifiée).')
    say('  '+('✓ Job Jenkins accessible.' if has_job else '! Job Jenkins absent ; configuration requise ensuite.'))
    if not _broker_ready():
        say('  ! Broker pas prêt : sudo autokom runtime reconcile --apply')
        return 1
    if not _broker_identity():
        say('  ! Broker actif, mais authentification Jenkins non confirmée depuis le socket.')
        say('  › Réparation : sudo autokom runtime jenkins-token --apply')
        return 1
    say('  ✓ Identité Jenkins confirmée depuis le broker Unix ; aucune modification.')
    return 0


def apply():
    if os.geteuid() != 0:
        raise JenkinsTokenError('Utiliser sudo pour renouveler le token.')
    original = _configured()
    say('  ◈ AUTOKOM · ROTATION SÉCURISÉE DU TOKEN JENKINS')
    say('  › Compte conservé : '+original['AUTOKOM_JENKINS_USER'])
    say('  › Le nouveau token est validé avant toute écriture sur disque.')
    candidate = dict(original)
    candidate['AUTOKOM_JENKINS_API_TOKEN'] = _prompt_new_token()
    _identity(candidate)
    has_job = _job_access(candidate)
    say('  ✓ Nouveau token accepté par Jenkins (/whoAmI/api/json).')
    if has_job:
        say('  ✓ Accès au job Jenkins existant : confirmé.')
    else:
        say('  ! Job Jenkins introuvable : le créer/synchroniser après la rotation.')
    if candidate == original:
        say('  ✓ Token déjà enregistré ; rechargement contrôlé du broker pour éviter les secrets en mémoire périmés.')
        if not _broker_ready():
            raise JenkinsTokenError('Broker non prêt : sudo autokom runtime reconcile --apply.')
        _systemctl('restart', BROKER_UNIT)
        if not _broker_ready() or not _broker_identity():
            raise JenkinsTokenError('Token valide, mais identité non confirmée depuis le broker après redémarrage.')
        say('  ✓ Broker actualisé ; identité Jenkins vérifiée via le socket Unix.')
        return 0
    if not _broker_ready():
        raise JenkinsTokenError('Broker non prêt avant rotation ; exécuter sudo autokom runtime reconcile --apply.')
    try:
        archived = backup(ENV_PATH)
        write_env(ENV_PATH, candidate)
    except Exception as exc:
        raise JenkinsTokenError('Sauvegarde/écriture sécurisée impossible ; aucun service redémarré.') from exc
    say('  ✓ Ancien fichier sauvegardé en zone root privée (aucun secret affiché).')
    try:
        _systemctl('restart', BROKER_UNIT)
        if not _broker_ready():
            raise JenkinsTokenError('Le broker ne répond pas après le renouvellement du token.')
        if not _broker_identity():
            raise JenkinsTokenError('Le broker ne confirme pas la nouvelle identité Jenkins.')
    except JenkinsTokenError as first_error:
        # Reinstall the original config from in-memory data, never a leaked token.
        restored = False
        try:
            write_env(ENV_PATH, original)
            _systemctl('restart', BROKER_UNIT)
            restored = _broker_ready()
        except Exception:
            pass
        if restored:
            raise JenkinsTokenError('Activation refusée ; ancienne configuration restaurée. '+str(first_error)) from first_error
        raise JenkinsTokenError('Activation refusée et retour arrière non confirmé. Consulter journalctl -u '+BROKER_UNIT+'.') from first_error
    say('  ✓ Token enregistré dans runtime.env ; broker rechargé et socket vérifié.')
    say('  ✓ Django, MySQL, Nginx, Grafana et UFW non modifiés.')
    say('  › Suite : sudo autokom runtime agent --apply (si agent isolé absent)')
    say('  › Validation : sudo autokom runtime doctor')
    return 0


def main(*, apply_mode=False):
    return apply() if apply_mode else check()
