# AUTOKOM Platform — gestion des administrateurs

## Objectif

Les administrateurs Django ne sont pas des utilisateurs clients. Ils utilisent
`AUTH_USER_MODEL = administrators.Administrator` et ont accès au Django Admin,
pas aux API JWT des clients.

## Commandes

```bash
sudo autokom app admin                  # menu local interactif
sudo autokom app admin create           # e-mail + mot de passe (saisie masquée)
sudo autokom app admin list             # lecture seule, privilèges root
sudo autokom app admin reset-password --email admin@example.com
sudo autokom app admin disable --email admin@example.com
sudo autokom app admin enable --email admin@example.com
```

L'authentification par passphrase AUTOKOM reste obligatoire pour les opérations
sensibles. Le mot de passe du compte Django est validé par les règles Django et
doit comporter au moins 14 caractères. Les mots de passe ne sont acceptés ni
par argument CLI ni par variable d'environnement supplémentaire.

Le backend Django est exécuté sous `autokom` avec le runtime `systemd`, ou dans
le conteneur non privilégié configuré par `docker-compose.autokom.yml`.

## Garde-fous

- `python manage.py createsuperuser` est refusé avec une explication ; le
  formulaire d'ajout/suppression d'administrateur dans Django Admin est désactivé.
- Pas de création implicite de compte admin pendant `app setup` ou `app deploy`.
- Pas de suppression physique. `disable` bloque le compte et ne peut pas
  désactiver le dernier superutilisateur actif.
- Journal métier `AuditEvent` pour création, consultation, réinitialisation,
  désactivation et réactivation (sans mot de passe).
- Le compte applicatif MySQL et les secrets de `/etc/autokom/` restent inchangés.
- Pas de migration créée ni exécutée par ce patch.

**Limite de sécurité** : un administrateur root du système ou quelqu'un ayant un
accès direct à la base de données / à un shell Django avec ses identifiants
peut contourner un garde-fou de commande. Un refus de `createsuperuser` n'est
pas une frontière de sécurité contre root ou un DBA. Les accès OS, SQL et au
compte de service doivent donc rester strictement limités.

## Appliquer depuis Kali

À la racine du dépôt et sur la branche `main` :

```bash
unzip -o /chemin/AUTOKOM_App_Admin_Django_Direct.zip
python3 -m unittest discover -s tests -p test_autokom_admin_cli.py -v
python3 -m compileall -q scripts/lib/autokom_admin_manager.py scripts/lib/autokom_app_manager.py platform/backend/helpers/admin_console.py
git diff --stat
git add scripts/lib/autokom_app_manager.py scripts/lib/autokom_admin_manager.py \
  platform/backend/manage.py platform/backend/helpers/admin_console.py \
  platform/backend/apps/accounts/admin/admin.py \
  tests/test_autokom_admin_cli.py docs/AUTOKOM-APP-ADMIN.md
git diff --cached --check
git commit -m 'feat: managed Django administrators via AUTOKOM CLI'
git push origin main
```

## Sur Ubuntu

```bash
sudo autokom update
sudo autokom app admin
sudo autokom app admin list
```

La mise à jour du code ne redémarre pas Gunicorn ; les opérations admin
s'exécutent sur Django et MySQL directement. Si le runtime est Docker, le
nouveau helper doit être présent dans l'image : lancer la reconstruction du
backend via `sudo autokom app deploy` avant la première création.

## Étape métier suivante

Reprendre progressivement `apps/projects/` : d'abord `git` (installation GitHub
et choix des dépôts autorisés), puis `project`, `configurations`, `environments`,
`secrets`, en conservant APIView (une méthode par classe), les schémas Swagger
par vue, et les autorisations organisation/projet.
