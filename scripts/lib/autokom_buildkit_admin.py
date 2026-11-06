#!/usr/bin/env python3
"""BuildKit isolated provisioner / read-only doctor for AUTOKOM runtime CLI."""
from __future__ import annotations
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import secrets
import shutil

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




def smoke_test(*, timeout=240, workspace_base=None):
    """Exercise an actual OCI RUN through the *managed* unprivileged daemon.

    A worker list alone does not test procfs mounts or OCI execution. This
    minimal build has no customer source or secrets. It only uses BuildKit's
    internal cache (no published image, no Docker socket, no exposed port).
    """
    import pwd
    builder = pwd.getpwnam('autokom-builder')
    base = Path(workspace_base) if workspace_base is not None else Path('/var/lib/autokom-jenkins-agent-v2')
    if base.is_symlink() or not base.is_dir():
        raise BuildkitError('Workspace isolé Jenkins absent ou non conforme.')
    parent = base.stat()
    if parent.st_uid != builder.pw_uid or (stat.S_IMODE(parent.st_mode) & 0o007):
        raise BuildkitError('Workspace Jenkins : propriétaire ou accès public inattendu.')
    if not BASE.joinpath('buildctl').is_file():
        raise BuildkitError('Binaire BuildKit AUTOKOM introuvable.')
    # Each run has a distinct cache key; a previously cached RUN must NOT
    # hide a broken procfs namespace after a host security policy changes.
    nonce = secrets.token_hex(12)
    try:
        with tempfile.TemporaryDirectory(prefix='.autokom-proc-smoke-', dir=base) as temporary:
            directory = Path(temporary)
            os.chown(directory, builder.pw_uid, builder.pw_gid)
            os.chmod(directory, 0o700)
            recipe = (b'FROM docker.io/library/busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662\n'
                      + b'ARG AUTOKOM_SMOKE=' + nonce.encode('ascii') + b'\n'
                      + b'RUN true && test -n "$AUTOKOM_SMOKE"\n')
            dockerfile = directory / 'Dockerfile'
            dockerfile.write_bytes(recipe)
            os.chmod(dockerfile, 0o644)
            cmd = ['runuser', '-u', 'autokom-builder', '--', 'env',
                   'BUILDKIT_HOST=' + ENDPOINT, str(BASE / 'buildctl'),
                   'build', '--frontend', 'dockerfile.v0',
                   '--local', 'context=' + str(directory),
                   '--local', 'dockerfile=' + str(directory),
                   '--progress=plain']
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                       check=False, env={'PATH': '/usr/local/bin:/usr/bin:/bin',
                                                         'LANG': 'C.UTF-8'})
    except subprocess.TimeoutExpired as exc:
        raise BuildkitError('Test RUN BuildKit expiré : vérifier réseau, mémoire et journal systemd.') from exc
    except OSError as exc:
        raise BuildkitError('Test RUN BuildKit non exécutable par le compte Jenkins.') from exc
    if completed.returncode:
        # Never emit raw builder logs into an end-user response. They can be
        # inspected by the administrator in the private BuildKit/Jenkins journal.
        output = ((completed.stdout or '') + '\n' + (completed.stderr or '')).lower()
        if ('mount too revealing' in output or 'error mounting "proc"' in output
                or "error mounting 'proc'" in output):
            raise BuildkitError('Test RUN : montage /proc refusé par le noyau. '
                                'Vérifier ProtectKernelTunables du service AUTOKOM BuildKit.')
        if 'failed to resolve source metadata' in output or 'i/o timeout' in output:
            raise BuildkitError('Test RUN : récupération de l’image de vérification impossible.')
        if 'no space left on device' in output:
            raise BuildkitError('Test RUN : espace disque ou quota de namespace insuffisant.')
        raise BuildkitError('Test RUN BuildKit échoué (code %d). '
                            'Consulter les journaux privés du moteur.' % completed.returncode)
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
    # Ansible already performed the real RUN test (fail-closed).
    print('  ✓ BuildKit rootless : worker et exécution OCI RUN vérifiés.')
    return 0
