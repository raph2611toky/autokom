#!/usr/bin/env python3
"""Conflict-safe one-time AUTOKOM Core CLI integration; does not rewrite other commands."""
import argparse
import ast
from pathlib import Path


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',required=True)
    ap.add_argument('--apply',action='store_true')
    opts=ap.parse_args()
    path=Path(opts.repo)/'scripts/lib/autokom_cli.py'
    source=path.read_text(encoding='utf-8')
    if "subcommand('runtime'," in source:
        print('✓ Commande autokom runtime déjà intégrée.');return
    before="    subcommand('app', help='Service Django AUTOKOM : setup, deploy, status, logs')"
    before2="    if arguments and arguments[0] == 'app':"
    if source.count(before)!=1 or source.count(before2)!=1:
        raise SystemExit('STOP : version CLI différente. Aucune modification.')
    source=source.replace(before,before+"\n    subcommand('runtime', help='Moteur client Docker/Jenkins/Unix socket')",1)
    source=source.replace(before2,"    if arguments and arguments[0] == 'runtime':\n        from autokom_runtime_admin import main as runtime_main, SetupError\n        try: return runtime_main(arguments[1:])\n        except SetupError as exc: raise CLIError(str(exc)) from exc\n"+before2,1)
    source=source.replace("(cmd=='app' and len(arguments)>1", "(cmd=='runtime' and '--apply' in arguments) or\n            (cmd=='app' and len(arguments)>1",1)
    ast.parse(source)
    if not opts.apply:
        print('✓ Preview : ajoute seulement autokom runtime et son contrôle de passphrase.');return
    path.write_text(source,encoding='utf-8')
    print('✓ AUTOKOM runtime intégré à la CLI ; aucune commande existante supprimée.')

if __name__=='__main__':main()
