# AUTOKOM — Mise à jour privée et service de la plateforme

Ce correctif concerne **AUTOKOM Core et le backend Django de la plateforme**. Il ne modifie
ni les APIs des projets clients ni les modules historiques d'installation d'infrastructure.

## 0. Préalables importants

- Restaurer `scripts/lib/autokom_cli.py` et les scripts AUTOKOM Core si un précédent commit
  les a supprimés. Ce correctif **refuse** d'inventer une CLI à partir de zéro.
- Ubuntu/Linux avec `python3-venv`, `acl`, `nginx`, `certbot`, `mysql-client`, Jenkins,
  Trivy et Semgrep selon le parcours déjà automatisé par AUTOKOM Core.
- DNS A/AAAA du domaine du backend pointant sur le serveur, ports 80 et 443 publics.
- Base **MySQL** obligatoire, aucune option SQLite.
- Avant la première mise en production : créer et **committer les migrations Django**.
  `autokom app deploy` ne lance jamais `makemigrations` en production.
- Préparer le fichier `platform/backend/.env` avec la configuration SMTP, MySQL si déjà présent,
  et les identifiants GitHub App du **service client**. Ne pas le committer.

## 1. Dépôt GitHub privé et authentification par code

1. Basculer le dépôt `raph2611toky/autokom` vers **Private** sur GitHub.
2. Créer de préférence une **GitHub App dédiée aux mises à jour du moteur**, distincte
   de la GitHub App par laquelle les clients choisissent leurs dépôts.
3. Sur l'App de mise à jour : activer **Enable Device Flow** et `Contents: Read-only`.
4. Installer cette App sur le dépôt privé **autokom** (Selected repositories).
5. Récupérer le **Client ID**, pas le Client secret, pas la clé privée.
6. Configurer le Client ID avec `autokom app setup --update-github-client-id ...`
   ou manuellement (fichier appartenant à root) :

   ```bash
   sudo install -d -m 700 /etc/autokom
   echo 'AUTOKOM_UPDATE_GITHUB_CLIENT_ID=VOTRE_CLIENT_ID' | sudo tee /etc/autokom/update.env >/dev/null
   sudo chmod 600 /etc/autokom/update.env
   ```

7. Vérifier : `sudo autokom update --check`; mise à jour : `sudo autokom update`.
   Un code apparaîtra à chaque exécution, à valider sur `https://github.com/login/device`.

La commande vérifie que `origin` correspond au dépôt AUTOKOM en HTTPS, refuse les modifications
locales, récupère la branche courante et n'autorise qu'un fast-forward. Le jeton est éphémère,
passé à un `GIT_ASKPASS` temporaire, puis détruit. Aucun PAT durable stocké.

**Ne plus utiliser l'installation par `curl https://raw.githubusercontent.com/.../install.sh`**
quand le dépôt devient privé. Préférer un clone authentifié puis le lanceur AUTOKOM installé
sur le serveur. Les clients de la plateforme n'ont pas besoin d'accéder au dépôt Core.

## 2. Installation systemd par défaut

Depuis la racine du dépôt :

```bash
# Tester la structure du patch avant application
python3 /chemin/patch/appliquer_patch.py --repo .
python3 /chemin/patch/appliquer_patch.py --repo . --apply

# Préparation explicite du backend, du compte et du proxy
sudo autokom app setup \
  --domain api.votre-domaine.tld \
  --email contact@votre-domaine.tld \
  --frontend https://votre-frontend.tld \
  --update-github-client-id VOTRE_CLIENT_ID

# Si MySQL est présent et qu'aucune base AUTOKOM n'existe : ajouter --provision-db
# Sinon renseigner DB_NAME/DB_USER/DB_PASSWORD/DB_HOST dans platform/backend/.env

# Scanner et vérifier sans publier
sudo autokom app deploy --check

# Appliquer les migrations, collecter les fichiers statiques, relancer le service
sudo autokom app deploy

autokom app status
sudo autokom app logs --lines 100
```

Le compte Linux `autokom` est un compte système **sans shell interactif et sans sudo**.
Le code et l'environnement virtuel restent non modifiables par le compte du service.
Le service est exécuté par **systemd** (unité `autokom-backend.service`) sous cet utilisateur.
Les répertoires de données sont `/var/lib/autokom/{working,artifacts,media,staticfiles}`.
Le fichier de secrets du service est `/etc/autokom/platform-backend.env` (root:autokom, 0640),
et la configuration runtime se trouve dans `/etc/autokom/app-runtime.json`.
Le service Gunicorn écoute uniquement sur **127.0.0.1:8172**.

Nginx configure un vhost pour le domaine existant et Certbot tente d'émettre HTTPS.
Le script refuse d'écraser un autre vhost du même nom ou de prendre un domaine
réservé dans un autre fichier de configuration Nginx.

Note : le processus du backend peut écrire ses propres **données** ; il n'obtient pas le droit
d'éditer `ansible/`, `scripts/`, `platform/backend/config/`, les secrets admin ou l'intégralité
du dépôt Git. C'est une séparation de privilèges importante.

## 3. Backend Docker (option)

```bash
sudo autokom app setup \
  --runtime docker \
  --domain api.votre-domaine.tld \
  --email contact@votre-domaine.tld \
  --frontend https://votre-frontend.tld
sudo autokom app deploy
```

Il faut alors un **MySQL accessible depuis le réseau Docker**, configuré par `DB_HOST`.
La valeur `127.0.0.1` n'est pas utilisable depuis le conteneur ; le script la refuse.
Pour éviter une exposition inutile du MySQL local, le runtime par défaut reste systemd.
La migration entre systemd et Docker n'est pas automatique et nécessite une procédure
contrôlée (sauvegardes, volumes, base, interruption de service).

## 4. DevSecOps du backend lui-même

`sudo autokom app deploy` exécute :

1. Semgrep SAST (échec bloquant) ;
2. Trivy vulnérabilités et secrets (HIGH/CRITICAL bloquants) ;
3. contrôles Django de production ;
4. vérification de la présence des migrations dans le dépôt ;
5. migrations MySQL ;
6. collectstatic ;
7. redémarrage de Gunicorn (ou reconstruction Docker) ;
8. health check HTTP local `/api/health/`, incluant `SELECT 1` MySQL.

Le pipeline de référence `pipelines/autokom-backend/Jenkinsfile` vérifie en plus la compilation,
les tests Django et les contrôles DevSecOps. **Le job Jenkins, l'agent et les identifiants Git
privés devront être raccordés dans Jenkins** ; le fichier Jenkinsfile seul ne crée pas le job.

Le service n'a pas le droit de s'autodéployer en root. Les déploiements production restent
une opération administrative explicite (`sudo autokom app deploy`). Pour rendre l'étape
entièrement automatique depuis Jenkins, il faudra configurer une autorisation de déploiement
restreinte côté serveur (identité de déploiement séparée et mécanisme d'approbation).

## 5. Paramètres nécessaires dans `.env` (backend)

- `DB_ENGINE=mysql`, `DB_HOST=127.0.0.1`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` ;
- `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend` et SMTP ;
- éventuellement `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`,
  `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_CALLBACK_URL`, `AUTOKOM_FIELD_ENCRYPTION_KEY`.
- `FRONTEND_URL` est configuré par `--frontend` ; l'URL HTTPS du backend par `--domain`.

Le compte service récupère l'environnement depuis `/etc/autokom/platform-backend.env`.
Les secrets ne doivent jamais être ajoutés au commit Git.

## 6. Limites assumées et vérification

- L'opération ne provisionne pas magiquement un domaine ou son DNS ; il doit déjà pointer
  vers le serveur. Certbot a besoin d'une validation DNS/HTTP fonctionnelle.
- Le premier `deploy` exige des migrations Django déjà créées et versionnées.
- Il ne crée pas le job Jenkins ni ses identifiants privés ; le Jenkinsfile en définit le contrat.
- Les scanners peuvent nécessiter l'accès réseau à leurs bases de signatures et règles.
- Les migrations de données MySQL ne sont pas annulées par une simple restauration Git.
- Le système doit être testé sur une VM de préproduction avant usage réel.

Pour diagnostiquer : `sudo journalctl -u autokom-backend -n 100 --no-pager`,
`sudo nginx -t`, `sudo systemctl status autokom-backend`, `sudo autokom app status`,
`sudo autokom app logs`.
