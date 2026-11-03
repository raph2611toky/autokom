#!/usr/bin/env python3
"""Safely register AUTOKOM DNS into the installed CLI without overwriting it.

Direct-extract ZIP layout. One-time --apply integration is required because
AUTOKOM CLI varies between installed releases and must not be overwritten.
"""
from __future__ import annotations
import argparse
import ast
import difflib
from pathlib import Path

PARSER_ANCHOR = "    subcommand('app',"
DISPATCH_ANCHOR = "    if arguments and arguments[0] == 'app':"
AUTH_ANCHOR = "        mutating=("
PARSER_LINE = "    subcommand('dns', help='Zones BIND9, Cloudflare, environnements, vérifications DNS')\n"
DISPATCH = '''    if arguments and arguments[0] == 'dns':
        from autokom_dns import main as dns_main, DNSError
        try:
            return dns_main(arguments[1:])
        except DNSError as exc:
            raise CLIError(str(exc)) from exc
'''
AUTH_INSERT = '''        # AUTOKOM DNS shares the existing passphrase/OTP for sensitive actions.
        # Read-only checks and explicit --check simulations do not require it.
        dns_sensitive = (
            cmd == 'dns' and (
                len(arguments) == 1 or
                (len(arguments) > 1 and arguments[1] in ('install', 'setup')
                 and '--check' not in arguments) or
                (len(arguments) > 2 and arguments[1] == 'record'
                 and arguments[2] in ('add', 'delete') and '--apply' in arguments) or
                (len(arguments) > 2 and arguments[1] == 'tsig'
                 and arguments[2] == 'generate') or
                (len(arguments) > 2 and arguments[1] == 'cloudflare'
                 and arguments[2] in ('add', 'delete') and '--apply' in arguments)
            )
        )
'''

def patch(source: str) -> str:
    if "from autokom_dns import main as dns_main" in source:
        return source
    for key in (PARSER_ANCHOR, DISPATCH_ANCHOR, AUTH_ANCHOR):
        if source.count(key) != 1:
            raise RuntimeError(f'Ancrage CLI absent ou ambigu : {key.strip()}')
    updated = source.replace(PARSER_ANCHOR, PARSER_LINE + PARSER_ANCHOR, 1)
    updated = updated.replace(DISPATCH_ANCHOR, DISPATCH + DISPATCH_ANCHOR, 1)
    updated = updated.replace(AUTH_ANCHOR, AUTH_INSERT + '        mutating=(dns_sensitive or ', 1)
    ast.parse(updated)
    return updated

def main():
    p = argparse.ArgumentParser(description='Intégration sécurisée de autokom dns')
    p.add_argument('--repo', default='.')
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    path = Path(args.repo).resolve() / 'scripts/lib/autokom_cli.py'
    if not path.is_file(): raise SystemExit('Erreur : scripts/lib/autokom_cli.py absent.')
    old = path.read_text(encoding='utf-8')
    new = patch(old)
    if old == new:
        print('AUTOKOM DNS déjà intégré : aucune modification.')
        return
    if not (path.parent / 'autokom_dns.py').is_file():
        raise SystemExit('Module autokom_dns.py absent : extraire le ZIP depuis la racine du dépôt.')
    delta = list(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile='CLI actuelle', tofile='CLI + DNS', lineterm=''))
    print('\n'.join(delta[:95]))
    if args.apply:
        path.write_text(new, encoding='utf-8')
        print('OK : autokom dns enregistré sans écraser les autres commandes.')
    else:
        print('SIMULATION : ajouter --apply pour activer autokom dns.')

if __name__ == '__main__':
    main()
