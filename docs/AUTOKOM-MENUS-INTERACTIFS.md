# AUTOKOM — menus interactifs unifiés

Toutes les décisions à choix multiples de `autokom app admin`, `autokom app setup`
et de la proposition de réparation `autokom app doctor` utilisent maintenant
le composant existant `scripts/lib/menu.py` (`pick`). Il affiche le curseur
vert, accepte ↑/↓, Entrée, les raccourcis 1–9 et permet d’annuler avec q/0
ou Ctrl-C sans conserver le menu statique dans le terminal.

Les champs de texte libre (e-mail, noms de domaine, port, chemins) restent
saisis au clavier. Les actions sensibles conservent leur passphrase et, dans
le cas de la configuration MySQL, leur mot de confirmation explicite.

Les commandes directes et les options non interactives sont conservées pour
l'automatisation Ansible et les scripts. Aucune modification de service,
de base MySQL, de Django ou de droits Linux n'est effectuée par ce patch.

## Installation sur Kali

Depuis la racine du dépôt, extraire directement le ZIP :

```sh
unzip -o /chemin/AUTOKOM_Menus_Interactifs_Direct.zip
python3 -m unittest discover -s tests -p 'test_autokom_keyboard_menus.py' -v
python3 -m py_compile scripts/lib/autokom_admin_manager.py scripts/lib/autokom_app_manager.py scripts/lib/autokom_mysql_repair.py
```

Inspecter `git diff`, puis commit uniquement les fichiers de ce correctif
sur la branche `main`. Sur Ubuntu, `sudo autokom update` applique les sources.

Les navigateurs ANSI de `menu.py` nécessitent un vrai terminal (`/dev/tty`).
Sans TTY, utiliser des sous-commandes et options explicites.
