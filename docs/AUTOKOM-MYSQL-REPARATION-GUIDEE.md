# AUTOKOM — Réparation MySQL guidée et authentification à trois essais

## Portée

Ce correctif concerne seulement AUTOKOM Core et son propre backend Django. Il conserve `autokom update`, l'authentification GitHub, l'assistant `app setup`, les animations d'installation, le pipeline DevSecOps et les journaux existants.

Les fichiers ZIP sont placés **directement dans l'arborescence du dépôt** : extraire à la racine d'AUTOKOM.

## Authentification

- `🔑 Passphrase credential :` avec saisie masquée.
- Trois saisies maximum **par commande**, et un total de cinq échecs au maximum avant un verrou de 15 minutes, toutes commandes confondues.
- Après un succès, le compteur des échecs est remis à zéro.
- Un verrou global déjà actif interdit tout nouvel essai, avec affichage du délai restant.
- La récupération par OTP/code de secours reste inchangée.

## Vérification et réparation MySQL

`autokom app doctor` contrôle les identifiants **réellement chargés par Django**. Si la connexion échoue en terminal interactif et que l'administrateur donne son accord :

1. Compare le fichier service `/etc/autokom/platform-backend.env` avec le fichier source `/etc/autokom/secrets/backend.env`.
2. Si les identifiants du fichier source fonctionnent, propose de les synchroniser avec la configuration du service (confirmation `SYNCHRONISER`).
3. Sinon, **uniquement pour MySQL local `127.0.0.1:3306` ou `localhost:3306`**, vérifie que le socket administrateur root est accessible.
4. Propose de créer un compte applicatif neuf `autokom_backend` et, seulement si nécessaire, de créer la base spécifiée. Le mot de passe fort est généré par AUTOKOM et stocké dans les fichiers `.env` sous permissions `0600` (confirmation `CONFIGURER`). Le compte initial `autokom` reste intact.
5. Accorde des privilèges **sur cette seule base** adaptés à la lecture, l'écriture et aux migrations Django. Ne donne jamais de privilèges globaux ni `GRANT OPTION`.
6. Teste ensuite `SELECT 1` avec la configuration **Django** du compte système. Sans validation finale, le déploiement reste bloqué.

**Ne réinitialise jamais le mot de passe d'un compte SQL préexistant**, même si celui du `.env` échoue. Si `autokom_backend` existe déjà, refuse toute nouvelle modification ; diagnostic administratif nécessaire.

Les secrets n'apparaissent ni dans les arguments des commandes, ni dans les logs, ni dans le ZIP. Les échanges sensibles avec les sous-processus Python passent par stdin. Le programme n'invoque pas de shell pour composer du SQL.

## Fonctionnement en production

```bash
sudo autokom app doctor                # diagnostic, puis proposition si erreur/TTY
sudo autokom app doctor --no-repair    # strictement lecture seule
sudo autokom app deploy --check       # contrôles sans réparation ni déploiement
sudo autokom app deploy               # réparation si acceptée, puis contrôles, migrations, service
sudo autokom app status
```

**Prérequis du provisionnement** : compte root Unix autorisé à accéder à MySQL 8.x par socket local et `mysqlclient` dans `/opt/autokom/venv`. Si le root MySQL n'est pas utilisable sans mot de passe, AUTOKOM s'arrête sans changement ; il ne cherche pas à contourner les politiques DBA.

**Sécurité opérationnelle** : le compte applicatif est autorisé à faire les migrations car le même compte exécute Django et `migrate` dans l'architecture actuelle. À terme, séparer les identifiants DBA de migration et ceux du runtime, moins privilégiés.

## Installation depuis Kali (branche main)

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM_MySQL_Repair_Auth3_Direct.zip
python3 -m py_compile scripts/lib/autokom_security.py scripts/lib/autokom_app_manager.py scripts/lib/autokom_mysql_repair.py
python3 -m unittest discover -s tests -p 'test_auth_mysql_repair.py' -v
git diff --stat
git add scripts/lib/autokom_security.py scripts/lib/autokom_app_manager.py scripts/lib/autokom_mysql_repair.py tests/test_auth_mysql_repair.py docs/AUTOKOM-MYSQL-REPARATION-GUIDEE.md
git diff --cached --check
git commit -m 'fix: guided MySQL repair and three passphrase attempts'
git push origin main
```

Sur Ubuntu, `sudo autokom update` puis `sudo autokom app doctor` ou `sudo autokom app deploy`. **Aucun `app setup` ni `apt install` supplémentaire n'est nécessaire.**

## Validation

- Tests automatisés sans accès à MySQL réel : trois essais, succès après erreurs, verrou global, refus sans TTY, refus si compte existant, synchronisation, création sur confirmation.
- Tests de régression : UI de déploiement, Semgrep local, vérification Django, rapport Git.
- **Non validé sur l'instance Ubuntu** : accès root socket MySQL, droits du serveur, mutations DBA et redémarrage Gunicorn. Le premier usage se fait sous supervision de l'administrateur.

## Confidentialité

Ne jamais committer `backend.env`. Les secrets apparus dans un journal, un ticket ou une conversation doivent être considérés exposés et renouvelés. Procéder prudemment pour les clés de chiffrement contenant potentiellement des données chiffrées : migrer/rechiffrer avant suppression de l'ancienne clé.
