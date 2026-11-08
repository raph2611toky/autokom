#!/usr/bin/env python3
"""Convergent recovery of the AUTOKOM deployment control plane.

No web request launches privileged commands. Only the root AUTOKOM CLI can
reconcile *known* systemd units, and never changes credentials, firewall rules
or the Django application database. Socket health is checked as the real
unprivileged autokom account, not merely by looking for a filename.
"""
from __future__ import annotations

from dataclasses import dataclass
import grp
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time

from cli_colors import pretty_print as say

ROOT = Path(__file__).resolve().parents[2]
SOCKET = Path('/run/autokom/platform-control.sock')
RUNTIME_ENV = Path('/etc/autokom/secrets/runtime.env')
BACKEND_ENV = Path('/etc/autokom/secrets/backend.env')
BROKER_UNIT = 'autokom-platform-broker.service'
WORKER_UNIT = 'autokom-deployment-worker.service'
BROKER_TEMPLATE = ROOT / 'scripts/systemd/autokom-platform-broker.service'
WORKER_TEMPLATE = ROOT / 'platform/backend/deployment_templates/autokom-deployment-worker.service.example'
UNIT_DIR = Path('/etc/systemd/system')


class ControlRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Health:
    broker: str
    worker: str
    socket: str
    ready: bool


# Never log exceptions / command arguments: they may contain sensitive paths or
# environment-file contents. The response itself contains only these tokens.
PROBE_CODE = r'''import json,socket,sys
try:
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
  sock.settimeout(3)
  sock.connect('/run/autokom/platform-control.sock')
  sock.sendall(b'{"action":"ping","params":{}}\n')
  response=b''
  while not response.endswith(b'\n') and len(response)<8192:
   chunk=sock.recv(8192)
   if not chunk:break
   response+=chunk
  answer=json.loads(response)
  sys.exit(0 if answer.get('ok') and isinstance(answer.get('result'),dict) else 7)
except PermissionError:sys.exit(4)
except (FileNotFoundError,ConnectionRefusedError):sys.exit(5)
except TimeoutError:sys.exit(6)
except (OSError,ValueError,KeyError,TypeError):sys.exit(7)
'''


def _run(argv, *, timeout=15):
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlRecoveryError('Commande système indisponible : '+str(argv[0])) from exc


def _active(unit):
    result = _run(['systemctl','is-active',unit])
    return 'active' if result.returncode == 0 and result.stdout.strip() == 'active' else 'inactive'


def _installed(unit):
    result = _run(['systemctl','show',unit,'--property=LoadState','--value'])
    return result.returncode == 0 and result.stdout.strip() == 'loaded'


def _socket_status():
    if not SOCKET.parent.is_dir() or SOCKET.parent.is_symlink():
        return 'directory-missing'
    try:
        group = grp.getgrnam('autokom').gr_gid
        user = pwd.getpwnam('autokom')
        directory = SOCKET.parent.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
                or directory.st_gid != group or stat.S_IMODE(directory.st_mode) != 0o750):
            return 'directory-permissions'
        status = SOCKET.lstat()
        if not stat.S_ISSOCK(status.st_mode):
            return 'socket-invalid'
        if status.st_uid != 0 or status.st_gid != group or stat.S_IMODE(status.st_mode) != 0o660:
            return 'socket-permissions'
        if user.pw_gid != group:
            return 'account-group'
    except (FileNotFoundError, KeyError):
        return 'socket-missing'
    try:
        result = _run(['runuser','-u','autokom','--',sys.executable,'-c',PROBE_CODE],timeout=7)
    except ControlRecoveryError:
        return 'probe-unavailable'
    return {
        0:'ok', 4:'permission-denied', 5:'socket-refused',
        6:'timeout', 7:'protocol-invalid'
    }.get(result.returncode,'probe-unavailable')


def inspect():
    broker = _active(BROKER_UNIT)
    worker = _active(WORKER_UNIT)
    socket = _socket_status() if broker == 'active' else 'broker-inactive'
    return Health(broker=broker, worker=worker, socket=socket,
                  ready=(broker == 'active' and worker == 'active' and socket == 'ok'))


def _environment_preflight():
    # The broker must never be started with guessed or duplicated Jenkins secrets.
    if RUNTIME_ENV.is_symlink() or not RUNTIME_ENV.is_file():
        raise ControlRecoveryError('Secrets du broker absents : sudo autokom runtime env setup (une fois).')
    s=RUNTIME_ENV.stat()
    if s.st_uid != 0 or stat.S_IMODE(s.st_mode) != 0o600:
        raise ControlRecoveryError('Secrets du broker non conformes : runtime.env doit être root:0600.')
    if BACKEND_ENV.is_file():
        from autokom_env_manager import read_env
        try:
            backend = read_env(BACKEND_ENV)
        except Exception as exc:
            raise ControlRecoveryError('Configuration backend.env illisible ou non conforme.') from exc
        configured = backend.get('AUTOKOM_CONTROL_SOCKET','/run/autokom/platform-control.sock')
        if configured != str(SOCKET):
            raise ControlRecoveryError('Socket du backend différent du socket du broker : vérifier AUTOKOM_CONTROL_SOCKET.')
    try:
        pwd.getpwnam('autokom')
        grp.getgrnam('autokom')
    except KeyError as exc:
        raise ControlRecoveryError('Compte/groupe autokom absent : provisionner le runtime sécurisé.') from exc
    binary = ROOT / 'scripts/lib/autokom_platform_broker.py'
    if not binary.is_file() or binary.is_symlink():
        raise ControlRecoveryError('Code du broker absent du dépôt AUTOKOM actif.')
    if not (ROOT/'platform/backend/manage.py').is_file():
        raise ControlRecoveryError('Code Django absent du dépôt AUTOKOM actif.')


def _install_missing_unit(unit, template):
    dest = UNIT_DIR / unit
    if dest.is_symlink():
        raise ControlRecoveryError('Service systemd symbolique non autorisé : '+unit)
    if dest.exists():
        # Never override local service modifications, even when the service is broken.
        raise ControlRecoveryError('Service déjà présent mais non chargé : '+unit+' ; vérifier systemctl cat.')
    if not template.is_file() or template.is_symlink():
        raise ControlRecoveryError('Modèle systemd AUTOKOM absent : '+unit)
    text = template.read_text(encoding='utf-8')
    if unit == BROKER_UNIT and '/opt/autokom/source/scripts/lib/autokom_platform_broker.py' not in text:
        raise ControlRecoveryError('Modèle du broker non conforme au chemin AUTOKOM actif.')
    if unit == WORKER_UNIT and 'autokom_deployment_worker' not in text:
        raise ControlRecoveryError('Modèle du worker non conforme.')
    try:
        fd = os.open(dest, os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0), 0o644)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(text)
        os.chmod(dest,0o644)
    except FileExistsError as exc:
        raise ControlRecoveryError('Service apparu pendant la réparation : '+unit) from exc
    say('  + Service AUTOKOM installé : '+unit)


def _wait_for_broker(attempts=12):
    for _ in range(attempts):
        if _active(BROKER_UNIT) == 'active' and _socket_status() == 'ok':
            return True
        time.sleep(0.4)
    return False


def reconcile(*, apply=False):
    """Return a health snapshot; no mutation in --check mode.

    No new external listener or privilege is ever granted. The command only
    repairs known systemd services under an already provisioned root runtime.
    """
    before = inspect()
    if before.ready:
        say('  ✓ Contrôle des déploiements : broker, socket Unix et worker prêts')
        return before
    say('  ! Contrôle des déploiements : broker='+before.broker+
        ' · socket='+before.socket+' · worker='+before.worker)
    if not apply:
        say('  › Réparation nécessaire : sudo autokom recover --apply')
        return before
    if os.geteuid() != 0:
        raise ControlRecoveryError('Réparation réservée à root (sudo).')
    _environment_preflight()
    missing = []
    for unit, template in ((BROKER_UNIT,BROKER_TEMPLATE),(WORKER_UNIT,WORKER_TEMPLATE)):
        if not _installed(unit):
            missing.append((unit,template))
    for unit,template in missing:
        _install_missing_unit(unit,template)
    if missing:
        if _run(['systemctl','daemon-reload'], timeout=20).returncode:
            raise ControlRecoveryError('systemd daemon-reload a échoué.')
    if before.broker != 'active':
        say('  › Démarrage du broker de contrôle AUTOKOM')
        if _run(['systemctl','enable','--now',BROKER_UNIT],timeout=25).returncode:
            raise ControlRecoveryError('Broker refusé par systemd : sudo journalctl -u autokom-platform-broker -n 80 --no-pager')
    elif before.socket != 'ok':
        # Restart once, rather than loop or chmod/chown a possibly forged socket.
        if before.socket in ('socket-invalid','directory-permissions','socket-permissions'):
            raise ControlRecoveryError('Permissions/socket non conformes : réparation automatique refusée.')
        say('  › Broker actif sans socket prêt : un redémarrage contrôlé')
        if _run(['systemctl','restart',BROKER_UNIT],timeout=25).returncode:
            raise ControlRecoveryError('Redémarrage du broker refusé : consulter son journal systemd.')
    if not _wait_for_broker():
        raise ControlRecoveryError('Broker non prêt après démarrage : sudo journalctl -u autokom-platform-broker -n 80 --no-pager')
    if _active(WORKER_UNIT) != 'active':
        say('  › Réactivation du worker de déploiement AUTOKOM')
        if _run(['systemctl','enable','--now',WORKER_UNIT],timeout=25).returncode:
            raise ControlRecoveryError('Worker non démarré : sudo journalctl -u autokom-deployment-worker -n 80 --no-pager')
    after=inspect()
    if not after.ready:
        raise ControlRecoveryError('Contrôle toujours incomplet : broker='+after.broker+
                                   ' · socket='+after.socket+' · worker='+after.worker)
    say('  ✓ Broker, socket Unix et worker restaurés : test réussi avec le compte autokom')
    return after
