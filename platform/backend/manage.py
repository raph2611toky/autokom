#!/usr/bin/env python3
import os
import sys

def main():
    # Les administrateurs se créent uniquement via la commande AUTOKOM.
    # Ce garde-fou vise l'interface manage.py standard ; root/DBA restent
    # intrinsèquement des acteurs privilégiés du système.
    if len(sys.argv) > 1 and sys.argv[1] == 'createsuperuser':
        print('Création directe désactivée. Utiliser : sudo autokom app admin create',
              file=sys.stderr)
        raise SystemExit(2)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
