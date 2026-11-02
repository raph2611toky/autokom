#!/usr/bin/env python3
"""Replace old AUTOKOM Platform backend code without backups or modifying MySQL.
Default: dry-run. Require --apply to delete prior managed code.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

MANAGED = ('apps', 'config', 'helpers', 'templates', 'statics', 'tests')
FILES = ('manage.py', 'requirements.txt', 'README.md', 'CHANGEMENTS.md', 'verify_structure.py', 'verify_static.py', 'model_inventory.json')
PRESERVE = ('.env', 'media', '.venv', 'venv', 'staticfiles', 'node_modules')


def fail(message):
    raise SystemExit('ERREUR: ' + message)


def main():
    p=argparse.ArgumentParser(description='Nettoyage/autoinstallation AUTOKOM backend, sans sauvegarde')
    p.add_argument('--zip', required=True, type=Path, help='Archive V2 contenant backend/')
    p.add_argument('--backend', required=True, type=Path, help='Chemin ABSOLU du backend existant')
    p.add_argument('--apply', action='store_true', help='ATTENTION : supprime le code existant et installe la V2')
    a=p.parse_args()
    zp=a.zip.expanduser().resolve(); target=a.backend.expanduser().resolve()
    if not zp.is_file() or not zipfile.is_zipfile(zp): fail('ZIP invalide')
    if not a.backend.is_absolute(): fail('--backend doit être un chemin absolu')
    if str(target) in ('/', '/home', '/root', '/tmp', '/mnt/data') or len(target.parts) < 5:
        fail('Répertoire cible insuffisamment précis')
    if target.name != 'backend': fail('Le dossier cible doit s’appeler backend')
    if not target.is_dir(): fail('Backend existant introuvable')
    if not (target/'manage.py').is_file() or not (target/'apps').is_dir():
        fail('La cible ne ressemble pas à un backend AUTOKOM Django')
    # If migration history files exist, block destructive code removal entirely.
    old_migrations = [f for f in (target/'apps').rglob('migrations/[0-9]*.py') if f.is_file()]
    if old_migrations:
        fail('Migrations historiques détectées : opération refusée. Vérifier leurs dépendances et les données MySQL AVANT remplacement. Exemples : '+', '.join(str(x) for x in old_migrations[:3]))
    with zipfile.ZipFile(zp) as z:
        filenames=set(z.namelist())
        mandatory=['backend/manage.py','backend/config/settings.py','backend/apps/accounts/admin/apps.py',
                   'backend/apps/accounts/user/apps.py','backend/apps/plans/catalog/models.py',
                   'backend/apps/plans/entitlements/models.py', 'backend/verify_structure.py']
        for f in mandatory:
            if f not in filenames: fail('Fichier manquant dans le ZIP : '+f)
        if not all((x.startswith('backend/') or x.startswith('scripts/')) and '..' not in Path(x).parts and not x.startswith('/') for x in filenames):
            fail('Archive contenant des chemins non autorisés')
        print('Source:', zp)
        print('Cible :', target)
        print('Dossiers de code à remplacer:', ', '.join(MANAGED))
        print('Fichiers de code à remplacer:', ', '.join(FILES))
        print('CONSERVÉS:', ', '.join(PRESERVE))
        print('MySQL : aucune opération SQL')
        if not a.apply:
            print('\nSIMULATION : aucun fichier modifié. Ajouter --apply pour exécuter.')
            return
        # Extract to staging on same parent filesystem. NEVER create an archival backup.
        with tempfile.TemporaryDirectory(prefix='.autokom_install_', dir=str(target.parent)) as stage:
            stage=Path(stage)
            for info in z.infolist():
                if not info.filename.startswith('backend/'):
                    continue
                out=stage/info.filename
                if info.is_dir():
                    out.mkdir(parents=True,exist_ok=True)
                else:
                    out.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(info) as inp, out.open('wb') as dest:
                        shutil.copyfileobj(inp,dest)
            new=stage/'backend'
            if not (new/'verify_structure.py').is_file(): fail('Extraction incomplète')
            # Remove only known managed code paths, never media/credentials/virtualenv or other directories.
            for name in MANAGED:
                current=target/name
                if current.is_symlink(): fail('Refus de supprimer un lien symbolique : '+str(current))
                if current.is_dir(): shutil.rmtree(current)
                elif current.exists(): current.unlink()
            for name in FILES:
                current=target/name
                if current.is_symlink(): fail('Refus de remplacer un lien symbolique : '+str(current))
                if current.is_file(): current.unlink()
            for name in MANAGED:
                source=new/name
                if source.exists(): shutil.copytree(source,target/name)
            for name in FILES:
                source=new/name
                if source.is_file(): shutil.copy2(source,target/name)
        print('\nOK : nouveau code installé, ancien code géré supprimé. Aucun backup créé.')
        print('Vérifier : cd', target, '&& python3 verify_structure.py')
        print('Ensuite vérifier MySQL et les migrations avant migrate.')

if __name__=='__main__':
    main()
