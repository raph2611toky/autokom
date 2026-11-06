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




def _smoke_failure_code(output):
    """Map only expected *public smoke-test* output to administrator-safe categories."""
    value = (output or '').lower()
    if any(fragment in value for fragment in (
            'error mounting "proc"', "error mounting 'proc'",
            'mount too revealing', 'mount proc:/proc')):
        return ('PROCFS_DENIED',
                'Montage /proc refusé : inspecter le namespace de montage du service '
                '(ProtectKernelTunables, MountAPIVFS et autres masquages).')
    if any(fragment in value for fragment in (
            'apparmor', 'permission denied', 'operation not permitted')):
        return ('HOST_POLICY_DENIED',
                'Opération interdite par le noyau ou une politique hôte : inspecter le journal du noyau.')
    if any(fragment in value for fragment in (
            'failed to resolve source metadata', 'i/o timeout',
            'network is unreachable', 'no such host', 'tls handshake timeout')):
        return ('REGISTRY_UNAVAILABLE',
                'Image publique de vérification inaccessible : vérifier DNS, TLS et sortie réseau du démon.')
    if 'no space left on device' in value or 'disk quota exceeded' in value:
        return ('STORAGE_LIMIT',
                'Capacité de stockage ou quota de namespaces insuffisant.')
    if any(fragment in value for fragment in ('connection refused', 'dial unix', 'permission denied on unix socket')):
        return ('BUILDKIT_SOCKET', 'Connexion au socket BuildKit impossible.')
    return ('OCI_RUN_FAILED',
            'Le build OCI a échoué. Lire le journal de diagnostic privé (aucun secret client).')


def _save_private_probe_log(output):
    """Persist only bounded output from a fixed BusyBox probe, never a customer build.

    Root-owned private path. On a read-only/unsupported host, return None.
    """
    import datetime
    root = Path('/var/log/autokom/runtime')
    try:
        if os.geteuid() != 0 or root.is_symlink() or not root.is_dir():
            return None
        info = root.stat()
        if info.st_uid != 0 or (stat.S_IMODE(info.st_mode) & 0o002):
            return None
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        path = root / ('buildkit-smoke-' + stamp + '-' + secrets.token_hex(4) + '.log')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', errors='replace') as stream:
            stream.write('AUTOKOM OCI smoke test (public BusyBox, no customer source)\n')
            stream.write((output or '')[-131072:])
        return str(path)
    except (OSError, ValueError):
        return None


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
    phase = 'creation_workspace'
    try:
        with tempfile.TemporaryDirectory(prefix='.autokom-proc-smoke-', dir=base) as temporary:
            directory = Path(temporary)
            phase = 'droits_workspace'
            os.chown(directory, builder.pw_uid, builder.pw_gid)
            os.chmod(directory, 0o700)
            recipe = (b'FROM docker.io/library/busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662\n'
                      + b'ARG AUTOKOM_SMOKE=' + nonce.encode('ascii') + b'\n'
                      + b'RUN true && test -n "$AUTOKOM_SMOKE"\n')
            phase = 'ecriture_dockerfile'
            dockerfile = directory / 'Dockerfile'
            dockerfile.write_bytes(recipe)
            os.chmod(dockerfile, 0o644)
            cmd = ['runuser', '-u', 'autokom-builder', '--', 'env',
                   'BUILDKIT_HOST=' + ENDPOINT, str(BASE / 'buildctl'),
                   'build', '--frontend', 'dockerfile.v0',
                   '--local', 'context=' + str(directory),
                   '--local', 'dockerfile=' + str(directory),
                   '--progress=plain']
            phase = 'execution_buildctl'
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                       check=False, env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                                                         'LANG': 'C.UTF-8'})
    except subprocess.TimeoutExpired as exc:
        raise BuildkitError('Test RUN BuildKit expiré : vérifier réseau, mémoire et journal systemd.') from exc
    except OSError as exc:
        # The system error was previously reduced to a misleading generic message.
        # Only report the bounded operation name and errno, never the raw exception
        # (which may contain private host paths or command arguments).
        import errno as errno_module
        reason = errno_module.errorcode.get(exc.errno, 'INCONNU')
        raise BuildkitError(
            'OCI_OS_ERROR: opération=' + phase + ', errno=' + reason +
            '. Examiner les permissions du workspace, le service BuildKit et le compte builder.'
        ) from exc
    if completed.returncode:
        # The fixed probe never includes customer sources or secrets; nonetheless,
        # publish only the bounded error category, not raw OCI traces, to the CLI.
        output = (completed.stdout or '') + '\n' + (completed.stderr or '')
        code, explanation = _smoke_failure_code(output)
        private_report = _save_private_probe_log(output)
        suffix = (' Journal privé administrateur : ' + private_report
                  if private_report else ' Journal privé indisponible.')
        raise BuildkitError('Test RUN : ' + code + ' (rc=' + str(completed.returncode)
                            + '). ' + explanation + suffix)
    return True


def main(argv=None):
    """Stable entrypoint for Ansible: safe, diagnostic and fail-closed."""
    import sys
    if list(sys.argv[1:] if argv is None else argv) != ['--smoke-test']:
        print('Usage: autokom_buildkit_admin.py --smoke-test', file=sys.stderr)
        return 64
    try:
        healthcheck()
        smoke_test()
    except BuildkitError as exc:
        print('AUTOKOM_BUILD_SMOKE_FAILED: ' + str(exc), file=sys.stderr)
        return 2
    print('AUTOKOM_BUILD_SMOKE_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

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
