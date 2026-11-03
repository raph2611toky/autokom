#!/usr/bin/env python3
"""AUTOKOM manager patch: dry-run default, conservative 3-way merge, no services."""
from pathlib import Path
import argparse, os, subprocess, tempfile, sys, difflib
HERE=Path(__file__).resolve().parent
MODIFIED=('scripts/lib/autokom_app_manager.py','pipelines/autokom-backend/Jenkinsfile')
ADDED=('pipelines/autokom-backend/semgrep-rules.yml','tests/test_autokom_app_deploy_ui.py','docs/AUTOKOM-BACKEND-DEPLOY-UX.md')


def merge(repo, rel):
    source=(HERE/rel).read_bytes()
    base=(HERE/'base'/rel).read_bytes()
    dst=repo/rel
    if not dst.is_file() or dst.is_symlink():
        raise RuntimeError('Fichier attendu manquant/lien symbolique : '+rel)
    current=dst.read_bytes()
    if current == source: return None
    if current == base: return source
    # Preserve local changes, fail instead of overwriting a conflict.
    with tempfile.TemporaryDirectory() as folder:
        paths=[Path(folder)/p for p in ('local','base','desired')]
        for p,b in zip(paths,(current,base,source)): p.write_bytes(b)
        proc=subprocess.run(['git','merge-file','-p',*map(str,paths)],capture_output=True)
        if proc.returncode or b'<<<<<<<' in proc.stdout or b'>>>>>>>' in proc.stdout:
            raise RuntimeError('Conflit avec modifications locales : '+rel+' (aucun fichier modifié)')
        return proc.stdout if proc.stdout != current else None


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo',required=True,type=Path)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(); repo=args.repo.resolve()
    if not (repo/'autokom').is_file() or not (repo/'platform/backend/manage.py').is_file():
        raise RuntimeError('Dépôt AUTOKOM non reconnu.')
    changes={}
    for rel in MODIFIED:
        desired=merge(repo,rel)
        print(('MODIFIER' if desired is not None else 'INCHANGÉ')+' : '+rel)
        if desired is not None:changes[rel]=desired
    for rel in ADDED:
        dst=repo/rel
        if dst.is_symlink(): raise RuntimeError('Lien symbolique interdit : '+rel)
        source=(HERE/rel).read_bytes()
        if dst.exists() and dst.read_bytes()!=source:
            raise RuntimeError('Fichier existant différent : '+rel)
        if not dst.exists():changes[rel]=source
        print(('AJOUTER' if not dst.exists() else 'INCHANGÉ')+' : '+rel)
    if not args.apply:
        print('\nSimulation. Aucun fichier ni service modifié. Pour appliquer: --apply')
        return
    for rel,data in changes.items():
        dst=repo/rel; dst.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dst.parent,delete=False) as fd:
            fd.write(data); temp=Path(fd.name)
        os.chmod(temp,(dst.stat().st_mode & 0o777) if dst.exists() else 0o644)
        os.replace(temp,dst)
    print('Correctif appliqué. Vérifier git diff avant tout commit.')

if __name__=='__main__':
    try: main()
    except (RuntimeError,OSError,subprocess.SubprocessError) as e:
        print('ERREUR : '+str(e),file=sys.stderr);sys.exit(1)
