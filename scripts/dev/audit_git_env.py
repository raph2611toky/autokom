#!/usr/bin/env python3
"""Preserve .gitignore exceptions for Django apps while excluding all .env files.
Never read or output actual secret values.
"""
import argparse
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
RULES=('*.env','*.env.*','.env','.env.*','**/.env.example','**/*.env.example')

def main():
    parser=argparse.ArgumentParser(description='Vérifier et corriger les règles Git AUTOKOM')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    path=ROOT/'.gitignore'
    old=path.read_text(encoding='utf-8') if path.exists() else ''
    lines=[line for line in old.splitlines() if line.strip() not in ('!.env.example','!**/.env.example','!*.env.example')]
    missing=[rule for rule in RULES if rule not in lines]
    new='\n'.join([*lines,*missing]).strip()+'\n'
    if args.apply:
        path.write_text(new,encoding='utf-8')
        print('✓ .gitignore renforcé ; exceptions Django existantes préservées.')
    else:
        print('Vérification :',len(missing),'règles manquantes ; exclusions .env à vérifier.')
    tracked=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,check=False)
    if tracked.returncode:return
    offending=[]
    for raw in tracked.stdout.split(b'\x00'):
        if not raw:continue
        name=raw.decode('utf-8',errors='replace')
        base=Path(name).name
        if base=='.env' or base.startswith('.env.') or base.endswith('.env') or '.env.' in base:
            offending.append(name)
    for name in offending:print('⚠ Fichier toujours suivi dans Git :',name)
    if offending:
        print('  Désindexer sans supprimer les fichiers locaux :')
        print('  git rm --cached --ignore-unmatch -- '+ ' '.join(repr(x) for x in offending))
    else:print('✓ Aucun .env ni .env.example suivi dans Git.')

if __name__=='__main__':main()
