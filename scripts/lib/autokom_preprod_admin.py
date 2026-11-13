"""Primary CLI entry and strictly scoped preproduction CLI."""
from pathlib import Path
import os
import fcntl
import subprocess

ROOT = Path('/opt/autokom-preprod/source')
COMMAND = Path('/usr/local/bin/autokom-pre')
ORIGIN = 'https://github.com/raph2611toky/autokom.git'
UNITS = ('autokom-preprod-web.service','autokom-preprod-broker.service','autokom-preprod-worker.service')
WRAPPER = """#!/usr/bin/env bash
# AUTOKOM managed preprod CLI
set -euo pipefail
exec /usr/bin/python3 /opt/autokom-preprod/source/scripts/lib/autokom_preprod_admin.py "$@"
"""


def call(*args, **opts):
    subprocess.run(args, check=True, **opts)


def install_wrapper():
    if COMMAND.is_symlink() or (COMMAND.exists() and COMMAND.read_text() != WRAPPER):
        raise RuntimeError('autokom-pre existant non géré : aucune modification.')
    if not COMMAND.exists():
        fd = os.open(COMMAND, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
        with os.fdopen(fd, 'w') as stream:
            stream.write(WRAPPER)


def update_and_deploy(check=False):
    if check:
        print('Plan : checkout master, autokom-pre, base MySQL dédiée, services et HTTPS. Aucun changement.')
        if ROOT.is_dir():
            manage('check')
        return 0
    if os.geteuid() != 0:
        raise RuntimeError('Utiliser sudo autokom update preprod.')
    if ROOT.parent.is_symlink():
        raise RuntimeError('Répertoire préproduction non fiable.')
    ROOT.parent.mkdir(mode=0o755, exist_ok=True)
    for path in (ROOT.parent, ROOT):
        if path.is_symlink() or (path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
            raise RuntimeError('Checkout de préproduction non fiable.')
    fd = os.open(ROOT.parent / '.update.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _apply()
    finally:
        os.close(fd)


def _apply():
    if not ROOT.exists():
        call('git','clone','--branch','master','--single-branch',ORIGIN,str(ROOT),
             env={**os.environ,'GIT_LFS_SKIP_SMUDGE':'1','GIT_TERMINAL_PROMPT':'0'},timeout=240)
    else:
        if subprocess.run(['systemctl','is-active','--quiet',UNITS[2]]).returncode == 0:
            call('/usr/bin/python3',str(ROOT/'scripts/preprod/configure.py'),'busy',timeout=25)
            call('systemctl','stop',UNITS[2],timeout=30)
        from autokom_private_update import update
        if update(ROOT, preprod=True):
            raise RuntimeError('Mise à jour de master incomplète.')
    if not (ROOT/'scripts/lib/autokom_preprod_admin.py').is_file():
        raise RuntimeError('master ne contient pas encore ce correctif. Publier master avant update preprod.')
    manage('deploy')
    install_wrapper()
    from autokom_deepsast_admin import install
    install()
    result = subprocess.run(['/usr/bin/python3',str(ROOT/'scripts/preprod/configure.py'),'worker-configured'],timeout=20)
    if result.returncode == 4:
        print('Console prête. Pour connecter les accès dédiés une fois : sudo autokom-pre app setup.')
        return 0
    result.check_returncode()
    call('/usr/bin/python3',str(ROOT/'scripts/preprod/jenkins-job.py'),'--configured','--apply',timeout=150)
    manage('enable-worker')
    return 0


def manage(action):
    call('bash',str(ROOT/'scripts/preprod/manage.sh'),action)


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (['-v'], ['--version']):
        call('git', '-C', str(ROOT), 'log', '-1', '--format=AUTOKOM préproduction %h')
        return 0
    if not argv or argv in (['-h'],['--help']):
        print('autokom-pre update [--check] | app deploy [--check] | app frontend deploy --apply | app setup | app status | app logs | runtime doctor | devsecops check')
        print('Outils communs et Terraform restent administrés par autokom sur la VM principale.')
        return 0
    if argv in (['update'], ['update','--check']):
        return update_and_deploy('--check' in argv)
    if argv == ['app','setup']:
        if os.geteuid() != 0:
            raise RuntimeError('Utiliser sudo autokom-pre app setup.')
        call('/usr/bin/python3',str(ROOT/'scripts/preprod/configure.py'),'connect')
        return update_and_deploy()
    if argv in (['app','deploy','--check'], ['app','frontend','deploy','--check']):
        manage('check')
    elif argv in (['app','deploy'], ['app','frontend','deploy','--apply']):
        return update_and_deploy()
    elif argv in (['app','status'],['runtime','status'],['runtime','doctor'],['status']):
        call('systemctl','status',*UNITS,'--no-pager')
    elif argv in (['app','logs'],['runtime','logs']):
        call('journalctl',*[arg for unit in UNITS for arg in ('-u',unit)],'-n','60','--no-pager')
    elif argv == ['devsecops','check']:
        from autokom_deepsast_admin import main as deep
        return deep(['check'])
    else:
        raise RuntimeError('Commande non isolable ou inconnue. Consulter autokom-pre --help.')
    return 0


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc) if type(exc) is RuntimeError else 'Préproduction : étape non confirmée. Voir la sortie précédente.') from None
