#!/usr/bin/env python3
"""BuildKit isolated provisioner / read-only doctor for AUTOKOM runtime CLI."""
from __future__ import annotations
import os
from pathlib import Path
import socket
import stat
import subprocess

VERSION = 'v0.34.2'
BASE = Path('/opt/autokom/toolchains/buildkit') / VERSION / 'bin'
SOCKET = Path('/run/autokom-buildkit/buildkitd.sock')
ENDPOINT = 'unix://' + str(SOCKET)
UNIT = 'autokom-buildkit.service'


class BuildkitError(Exception):
    pass


def healthcheck(*, timeout=20):
    """Inspect exact local socket and OCI workers under the actual Jenkins identity."""
    import grp
    import pwd
    builder = pwd.getpwnam('autokom-builder')
    daemon = pwd.getpwnam('autokom-buildkit')
    group = grp.getgrnam('autokom-builder')
    if builder.pw_uid == daemon.pw_uid:
        raise BuildkitError('Identité Jenkins et démon BuildKit confondues.')
    if not BASE.joinpath('buildctl').is_file():
        raise BuildkitError('Client BuildKit AUTOKOM absent; relancer runtime agent --apply.')
    parent = SOCKET.parent
    if parent.is_symlink() or not parent.is_dir():
        raise BuildkitError('Répertoire UNIX BuildKit absent ou non conforme.')
    pstat = parent.stat()
    if (pstat.st_uid != daemon.pw_uid or pstat.st_gid != group.gr_gid
            or stat.S_IMODE(pstat.st_mode) != 0o750):
        raise BuildkitError('Permissions du répertoire socket BuildKit incorrectes.')
    if SOCKET.is_symlink() or not SOCKET.exists():
        raise BuildkitError('Socket UNIX BuildKit absent.')
    info = SOCKET.stat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != daemon.pw_uid
            or info.st_gid != group.gr_gid or (stat.S_IMODE(info.st_mode) & 0o007)):
        raise BuildkitError('Socket BuildKit non privé ou propriétaire inattendu.')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(SOCKET))
        cmd = ['runuser', '-u', 'autokom-builder', '--', 'env',
               'BUILDKIT_HOST='+ENDPOINT, str(BASE / 'buildctl'), 'debug', 'workers']
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildkitError('Démon BuildKit non joignable par le builder.') from exc
    if proc.returncode or 'ID' not in proc.stdout:
        raise BuildkitError('BuildKit ne présente aucun worker utilisable au builder.')
    return True


def provision(*, apply):
    from autokom_runtime_admin import ROOT, run_live
    playbook = ROOT / 'ansible/playbooks/buildkit-rootless.yml'
    inventory = ROOT / 'ansible/inventories/local/hosts.ini'
    environment = os.environ.copy()
    environment['ANSIBLE_CONFIG'] = str(ROOT / 'ansible/ansible.cfg')
    print('  ◈ BuildKit rootless AUTOKOM · daemon isolé et socket privé')
    print('  › Version figée : ' + VERSION + ' · téléchargements SHA-256 vérifiés')
    print('  › Aucun port TCP, aucun accès au socket Docker, aucun secret client')
    if not apply:
        syntax = subprocess.run(['ansible-playbook','-i',str(inventory),
                                 '--syntax-check',str(playbook)],
                                env=environment, capture_output=True, text=True, timeout=35)
        if syntax.returncode:
            raise BuildkitError('Playbook BuildKit invalide : consulter les diagnostics Ansible.')
        print('  ✓ Contrôle de syntaxe réussi · aucune modification système.')
        return 0
    run_live(['ansible-playbook','-i',str(inventory),str(playbook)],
             env=environment, label='Provisionnement automatique BuildKit rootless', timeout=900)
    healthcheck()
    print('  ✓ Moteur BuildKit accessible au compte Jenkins (workers disponibles).')
    return 0
