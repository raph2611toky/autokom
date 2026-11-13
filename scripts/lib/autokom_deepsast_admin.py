"""Install and verify DeepSAST on the primary host, outside both checkouts."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

BASE = Path('/opt/autokom-deepsast')
PROD = Path('/opt/autokom/source')
STAGE = Path('/opt/autokom-preprod/source')
REL = Path('models/deep_sast_model_3_Hbol')
SHA = 'befaff21716f1d4a6f06298ef97c54116538a6c16483795b62544e3eaab82062'
NAMES = ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
         'vocab.txt', 'model.safetensors')


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def check_model(source):
    for name in NAMES:
        if (source / name).is_symlink() or not (source / name).is_file():
            return False
    with (source / 'model.safetensors').open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == SHA


def probe(base=BASE):
    code = ('import json,sys,tempfile; from pathlib import Path; '
            'sys.path.insert(0,sys.argv[1]); from autokom_deep_sast import infer; '
            'tmp=tempfile.TemporaryDirectory(); root=Path(tmp.name); '
            '(root/"demo.py").write_text("print(123)"); '
            'report=infer(root,Path(sys.argv[2]),[{"path":"demo.py","start":{"line":1},"end":{"line":1}}]); '
            'assert report["status"]=="COMPLETE" and report["analyzed"]==1')
    run(['runuser', '-u', 'autokom-builder', '--', str(base / 'venv/bin/python'),
         '-I', '-c', code, str(PROD / 'scripts/runtime'), str(base / 'model')],
        timeout=150, env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})


def install():
    BASE.mkdir(mode=0o755, exist_ok=True)
    if BASE.is_symlink() or BASE.stat().st_uid != 0 or BASE.stat().st_mode & 0o022:
        raise RuntimeError('Répertoire DeepSAST non fiable.')
    BASE.chmod(0o755)
    fd = os.open(BASE / '.install.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (BASE / 'ready').is_file():
            probe()
            return
        repo = PROD if (PROD / REL / 'config.json').is_file() else STAGE
        if not check_model(repo / REL):
            run(['git','-C',str(repo),'lfs','install','--local'], timeout=45)
            run(['git','-C',str(repo),'-c','lfs.activitytimeout=120','lfs','pull',
                 '--include='+str(REL / 'model.safetensors'),'--exclude='], timeout=1800)
        if not check_model(repo / REL):
            raise RuntimeError('Poids LFS absents ou SHA‑256 différent.')
        with tempfile.TemporaryDirectory(prefix='model-', dir=BASE) as work:
            target = Path(work) / 'model'
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            for name in NAMES:
                shutil.copyfile(repo / REL / name, target / name)
                (target / name).chmod(0o644)
            if not (BASE / 'model').exists():
                target.rename(BASE / 'model')
        if not check_model(BASE / 'model'):
            raise RuntimeError('Modèle installé différent de la référence.')
        previous_umask = os.umask(0o022)
        try:
            run(['/usr/bin/python3','-m','venv',str(BASE / 'venv')], timeout=90)
            pip = str(BASE / 'venv/bin/pip')
            run([pip,'install','--index-url','https://download.pytorch.org/whl/cpu','torch>=2.6,<3'], timeout=1800)
            run([pip,'install','transformers==4.57.1','safetensors>=0.4.5,<1'], timeout=900)
        finally:
            os.umask(previous_umask)
        probe()
        with (BASE / 'ready').open('x') as stream:
            stream.write(SHA+'\n')
        (BASE / 'ready').chmod(0o644)
    finally:
        os.close(fd)


def enable_production():
    from autokom_env_manager import read_env, write_env, backup
    env_path = Path('/etc/autokom/secrets/backend.env')
    info = env_path.lstat()
    if env_path.is_symlink() or not env_path.is_file() or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('Configuration backend de production non fiable.')
    env = read_env(env_path)
    updates = dict(AUTOKOM_SECURITY_REPORTS_ENABLED='true', AUTOKOM_SECURITY_FEEDBACK_ENABLED='true')
    if all(env.get(k) == v for k, v in updates.items()):
        return
    backup(env_path)
    write_env(env_path, {**env, **updates})


def deploy_production():
    from types import SimpleNamespace
    from autokom_app_manager import VENV, deploy, load_state, run_as_service
    from autokom_frontend_manager import main as deploy_frontend
    if load_state().get('runtime') != 'systemd':
        raise RuntimeError('Activation automatique prévue pour Gunicorn systemd.')
    # Verify new settings and scans before recycling the production processes.
    deploy(SimpleNamespace(check=True))
    run_as_service([str(VENV/'bin/python'),'manage.py','shell','-c',
                    'from apps.deployments.runs.models import DeploymentRun, StackDeployment; import sys; '
                    'sys.exit(5 if DeploymentRun.objects.filter(status__in=["QUEUED","RUNNING","PREPARED"]).exists() '
                    'or StackDeployment.objects.filter(status__in=["QUEUED","PREPARING","ACTIVATING"]).exists() else 0)'],
                   timeout=30)
    run(['systemctl','restart','autokom-platform-broker.service'], timeout=45)
    deploy(SimpleNamespace(check=False))
    run(['systemctl','restart','autokom-deployment-worker.service'], timeout=45)
    run(['systemctl','is-active','--quiet','autokom-deployment-worker.service'], timeout=15)
    if deploy_frontend(['deploy','--apply']):
        raise RuntimeError('Frontend non publié. Le backend a été mis à jour.')


def main(argv):
    parser = argparse.ArgumentParser(prog='autokom devsecops')
    parser.add_argument('action', choices=('check','apply'))
    parser.add_argument('--deploy', action='store_true', help='Migrer et publier Django/React et recharger le broker et le worker')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RuntimeError('Utiliser sudo autokom devsecops.')
    if args.action == 'apply':
        if not (PROD / 'scripts/runtime/autokom_deep_sast.py').is_file():
            raise RuntimeError('Mettre d’abord main à jour avec sudo autokom update.')
        install()
        enable_production()
        if args.deploy:
            deploy_production()
            print('DeepSAST vérifié, Django et React publiés. Tester un nouveau run DevSecOps.')
        else:
            print('DeepSAST vérifié. Pour publier les changements : sudo autokom devsecops apply --deploy.')
    else:
        if args.deploy:
            parser.error('--deploy exige apply')
        if not (BASE / 'ready').is_file():
            raise RuntimeError('DeepSAST non installé : sudo autokom devsecops apply.')
        probe()
        print('DeepSAST : inférence locale sous autokom-builder vérifiée.')
    return 0
