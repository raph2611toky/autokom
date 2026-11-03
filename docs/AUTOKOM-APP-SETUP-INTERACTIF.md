# AUTOKOM Platform — `autokom app setup` interactif

## Fonctionnement

`autokom app setup` est l'initialisation du **backend de la plateforme**, pas un déploiement client.
L'installation ne touche ni aux playbooks Jenkins ni aux installations Grafana, Docker,
Loki, MySQL existantes. Le service Django sera déployé plus tard par `autokom app deploy`.

1. L'administrateur AUTOKOM entre sa passphrase dans la CLI existante.
2. Il sélectionne **systemd (défaut)** ou **Docker**, puis `production` ou `staging`.
3. AUTOKOM détecte en premier `/etc/autokom/secrets/backend.env`, sinon `/etc/autokom/platform-backend.env`.
4. Il propose de conserver ou éditer MySQL, SMTP et les paramètres de la GitHub App **des clients**.
   Les mots de passe et secrets sont demandés via `getpass` et ne sont pas affichés.
5. Il choisit le domaine backend (ex. `backend.auto.ik0m.tech`) et le frontend
   (ex. `https://auto.ik0m.tech`), le port interne, les workers et le type de reverse proxy :
   - Nginx géré par AUTOKOM, avec Certbot ou TLS externe ;
   - Nginx déjà existant, **non modifié**, avec TLS existant.
6. Il lit le plan et tape exactement `INSTALLER` pour confirmer.
7. Le playbook `ansible/playbooks/platform-backend.yml` pose les paquets système
   nécessaires uniquement pour le runtime choisi. Il ne configure pas Jenkins, les
   services existants ni la base MySQL existante.
8. AUTOKOM prépare `autokom` (utilisateur Linux non root), l'environnement Python / le
   conteneur, les droits des répertoires de travail, la configuration secrète root:root
   0600, systemd si choisi, Nginx si géré, et HTTPS si Certbot est choisi.
9. La config non secrète est enregistrée dans `/etc/autokom/app-runtime.json`.
10. L'administrateur lance `sudo autokom app deploy --check` puis
    `sudo autokom app deploy` pour les analyses, migrations et publication.

**Important :** `backend.env` d'origine n'est **jamais publié sur GitHub**, ni supprimé.
Il est lu et sa configuration est recopiée, avec normalisation des variables de production,
dans `/etc/autokom/platform-backend.env` (root:root, 0600). Les mots de passe,
les JWT et clés de chiffrement déjà utilisés sont conservés sans régénération.
Si l'original est accessible à d'autres utilisateurs (ex. mode 0644), son mode est
corrigé en 0600 **uniquement lorsque l'installation est confirmée**.

## Pour ce serveur AWS

- Frontend : `https://auto.ik0m.tech`
- Backend : `backend.auto.ik0m.tech`
- Fichier source : `/etc/autokom/secrets/backend.env`
- MySQL local : `127.0.0.1:3306` (conservé)
- Runtime conseillé : `systemd` (port local 8172, 2 workers par défaut)
- Gestion HTTPS : `certbot` si aucun vhost existant ne possède le domaine ;
  `existing` + `external` si le domaine est **déjà servi par Nginx avec HTTPS**.

Sur le serveur, après `autokom update` (authentification AUTOKOM + code GitHub) :

```bash
sudo autokom app setup
```

Pour une prévisualisation reproductible sans prompt :

```bash
sudo autokom app setup \
  --runtime systemd --environment production \
  --domain backend.auto.ik0m.tech \
  --frontend https://auto.ik0m.tech \
  --email admin@example.org \
  --env-file /etc/autokom/secrets/backend.env \
  --nginx-mode managed --tls-mode certbot \
  --check
```

Pour l'installation automatisée par Ansible, reprendre ces options sans `--check`
et ajouter `--yes` ; éviter toute valeur de secret en ligne de commande.

## Règles de sécurité et limites

- **Seul MySQL** est accepté ; une base existante n'est ni effacée, ni recréée.
- En mode Docker, `DB_HOST=127.0.0.1` est refusé (désigne le conteneur).
- Le mode `production` refuse HTTP seul.
- Le reverse proxy `existing` n'est pas modifié ; à l'administrateur de confirmer
  qu'il transmet vers le port local choisi et définit `X-Forwarded-Proto`.
- En mode `managed`, AUTOKOM refuse de prendre la place d'un virtual host
  tiers occupant le domaine, et exécute `nginx -t` avant rechargement.
- Le setup ne génère pas de migrations Django et ne lance pas `makemigrations`.
- Le lancement du déploiement avec migrations doit être testé sur la base choisie.
- Les scanners Semgrep/Trivy restent prévus dans `app deploy --check`.
- Aucun système d'installation ne garantit un rollback automatique d'une migration
  MySQL ni de changements effectués par Certbot : faire un contrôle pré-déploiement.
- Une instance AUTOKOM Backend est prise en charge sur cet hôte ; changer son runtime,
  domaine ou port exige une migration explicite, jamais un remplacement silencieux.

## Vérification de l'état

```bash
sudo autokom app status
sudo autokom app logs --lines 100
sudo nginx -t
sudo systemctl status autokom-backend --no-pager
```

Le frontend et les apps métier Django ne sont pas modifiés par ce patch.
