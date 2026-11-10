#!/usr/bin/env python3
"""Read-only Jenkins agent jspawnhelper diagnosis; optional explicit targeted restart.

Never restarts the controller, changes JVM flags, or modifies a Jenkins build.
An operator must confirm there are no active jobs before --repair.
"""
import argparse
import os
from pathlib import Path
import subprocess
import stat
import sys
import time

UNIT = 'autokom-jenkins-agent.service'


def run(*argv):
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=20, check=False)


def agent_pid(runner=run):
    result = runner('systemctl', 'show', UNIT, '--property=MainPID', '--value')
    if result.returncode or not result.stdout.strip().isdigit():
        raise RuntimeError('Unité Jenkins non disponible')
    pid = int(result.stdout.strip())
    if pid <= 0:
        raise RuntimeError('Agent Jenkins arrêté')
    return pid


def jvm_helper(pid, proc=Path('/proc')):
    # A JVM executable may be marked '(deleted)' after an in-place JDK upgrade.
    # Recover its original path without following the vanished inode.
    java = Path(os.readlink(proc / str(pid) / 'exe').removesuffix(' (deleted)'))
    if java.exists():
        java = java.resolve()
    helper = java.parent.parent / 'lib' / 'jspawnhelper'
    return java, helper


def inspect(pid, proc=Path('/proc')):
    java, helper = jvm_helper(pid, proc)
    if not helper.is_file():
        return {'pid': pid, 'java': str(java), 'helper': str(helper), 'healthy': False,
                'reason': 'jspawnhelper introuvable'}
    # Agent runs unprivileged; world execute is required on packaged JDK helper.
    executable = bool(helper.stat().st_mode & stat.S_IXOTH)
    try:
        stat = (proc / str(pid) / 'stat').read_text()
        ticks = int(stat.rsplit(') ', 1)[1].split()[19])
        boot = time.time() - float((proc / 'uptime').read_text().split()[0])
        process_start = boot + ticks / os.sysconf('SC_CLK_TCK')
        outdated = helper.stat().st_mtime > process_start + 1
    except (OSError, ValueError, IndexError):
        outdated = None
    reason = 'JDK modifié depuis le démarrage de l’agent' if outdated else 'jspawnhelper sans exécution' if not executable else 'aucune anomalie démontrée'
    return {'pid': pid, 'java': str(java), 'helper': str(helper),
            'healthy': not (outdated is True or not executable), 'reason': reason}


def main():
    parser = argparse.ArgumentParser(description='Diagnostiquer le spawn helper de Jenkins sans modifier les builds')
    parser.add_argument('--repair', action='store_true', help='Redémarrer uniquement l’agent Jenkins')
    parser.add_argument('--confirm-no-builds', action='store_true', help='Confirmer explicitement qu’aucun build n’est actif')
    args = parser.parse_args()
    try:
        pid = agent_pid()
        result = inspect(pid)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('AUTOKOM Jenkins agent :', exc, file=sys.stderr)
        return 2
    print(f"AUTOKOM Jenkins : PID={result['pid']} Java={result['java']}")
    print(f"  jspawnhelper={result['helper']}")
    print(f"  Diagnostic : {result['reason']}")
    if not args.repair:
        if not result['healthy']:
            print('  Correction : --repair --confirm-no-builds (uniquement hors build)')
        return 0 if result['healthy'] else 1
    if not args.confirm_no_builds:
        print('Refus : confirmer qu’aucun build n’est actif via --confirm-no-builds.', file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print('Refus : exécuter avec sudo.', file=sys.stderr)
        return 2
    restarted = run('systemctl', 'restart', UNIT)
    if restarted.returncode:
        print('Échec du redémarrage de l’agent Jenkins.', file=sys.stderr)
        return 2
    state = run('systemctl', 'is-active', UNIT)
    if state.returncode or state.stdout.strip() != 'active':
        print('Agent non actif après le redémarrage. Vérifiez journalctl.', file=sys.stderr)
        return 2
    print('Agent Jenkins redémarré ; contrôler sa connexion dans Jenkins avant un nouveau build.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
