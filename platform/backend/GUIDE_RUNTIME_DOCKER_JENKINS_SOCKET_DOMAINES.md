# AUTOKOM Client Runtime — Jenkins, Docker local, DNS, HTTPS

## Architecture en production (phase AWS, sans GCP)

- **Django / Gunicorn (User=autokom)** : API comptes, accès par organisation, projet, état, audit, gestion des domaines, file MySQL. Aucun Docker CLI, Nginx, Certbot, système de fichiers root ni exécution de shell.
- **Worker Django (User=autokom)** : dépile la file, demande au broker Unix de contacter Jenkins, traite les étapes et les logs et demande l'activation Docker/HTTPS. Pas de commandes système.
- **Broker AUTOKOM Core (root, socket `/run/autokom/platform-control.sock`, root:autokom 0660)** : seulement les actions pré-définies `jenkins.*`, `runtime.*`, `domain.*`. Identification locale via `SO_PEERCRED`, entrée JSON bornée, aucun paramètre shell ni chemin exécutable libre.
- **Jenkins (job de confiance `autokom-client-deploy`)** : sur un agent séparé `autokom-isolated` sous compte `autokom-builder` **sans accès au socket Docker**, compile avec BuildKit rootless, produit une archive Docker locale `/var/lib/autokom/build-artifacts/<run_uuid>.tar`. Il n'utilise **aucun registre privé**.
- **Docker hôte** : broker charge l'archive, crée un réseau Docker privé `--internal` par environnement, exécute un seul conteneur avec UID non root, sans capabilities, read-only, limites CPU/RAM/PIDs, logs Docker plafonnés ; stockage applicatif limité à /tmp (RAM), aucun volume persistant ni quota de disque global garanti ; port publié **sur 127.0.0.1 uniquement**. Docker ne sert pas de registre partagé.
- **Nginx / TLS** : un fichier indépendant `autokom-customer-<hostname>.conf` par domaine, `nginx -t`, rollback, Certbot webroot, rechargement. Aucun vhost utilisateur existant n'est remplacé.
- **High Availability** : la fonctionnalité est visible dans les endpoints et demandes d'offre payante, **mais l'activation reste bloquée** tant que GCP, le cluster et la facturation ne sont pas opérationnels. Aucun registre premium n'est installé.

## IMPORTANT — pas d'exécution client arbitraire sur AWS sans isolation validée

Même avec un Docker rootless builder, des images clients non fiables partagent le noyau de l'hôte. Pour une PaaS publique, ajouter une véritable isolation par VM/micro-VM, réseau egress, limites disque, vérification images et mécanismes anti-abus. L'architecture livrée permet un **pilote limité à des dépôts de confiance**. Elle n'est pas une promesse d'isolation multi-tenant comparable à un fournisseur cloud géré.

## Prérequis (ne pas sauter)

1. Docker / Nginx / Certbot / Ansible / Jenkins actifs. **Ne pas** réinstaller les services existants.
2. Un compte de service Jenkins avec API token, un job Pipeline approuvé, et un agent dédié `autokom-isolated` fonctionnant en `autokom-builder`, ayant Git, Python, Semgrep, Trivy et BuildKit **rootless** (client `buildctl` et daemon accessible sans privilèges), **sans** appartenance au groupe `docker`, ni accès `/var/run/docker.sock`.
3. **Jenkins Credentials** : `autokom-runner-key` (Secret Text, même clé que `AUTOKOM_RUNNER_SHARED_KEY` du backend), `autokom-github-app` (credential de lecture GitHub App). Ne jamais exposer le secret à une étape `docker build`.
4. `AUTOKOM_API_BASE=https://backend.auto.ik0m.tech` et `AUTOKOM_ARTIFACT_DIR=/var/lib/autokom/build-artifacts` sont disponibles sur l'agent local. L'espace partagé ne doit pas être exposé sur HTTP.
5. `platform/backend/.env` / fichier réel de service root:0600 ou root:autokom 0640 : `AUTOKOM_RUNNER_SHARED_KEY` (min 32 caractères), `AUTOKOM_CONTROL_SOCKET=/run/autokom/platform-control.sock` (optionnel, valeur par défaut). Pas de credentials Jenkins dans Django requis.
6. Fichier séparé `/etc/autokom/platform-runtime.env` root:root 0600 pour le broker, basé sur `scripts/dev/platform-runtime.env.example`. **Ne jamais committer ce fichier.**
7. L'adresse publique AWS doit être **stable (Elastic IP)** pour les domaines. Ports 80/443 entrants configurés. Le port Docker privé n'est **jamais** ouvert dans Security Groups.
8. Pour `ikom.mg`, attendre que dot.mg ait délégué la zone à Cloudflare. Pour des sous-domaines immédiatement utilisables, sélectionner uniquement une zone déjà active, par exemple sous `auto.ik0m.tech` si son compte Cloudflare est effectivement administré par IKOM.

## Installation Core (sur Kali, après extraction des ZIP dans la racine AUTOKOM)

```bash
python3 scripts/dev/activate_runtime_cli.py --repo "$PWD"
python3 scripts/dev/activate_runtime_cli.py --repo "$PWD" --apply
python3 -m unittest discover -s tests -p 'test_autokom_runtime_*.py' -v
```

Publier les modifications sur la branche `main`, puis sur Ubuntu :

```bash
sudo autokom update
sudo install -m 0600 -o root -g root scripts/dev/platform-runtime.env.example /etc/autokom/platform-runtime.env
sudoedit /etc/autokom/platform-runtime.env
sudo autokom runtime setup --check
sudo autokom runtime setup --apply
sudo autokom runtime jenkins --check
sudo autokom runtime jenkins --apply  # Avec sauvegarde XML du job existant
sudo autokom runtime status
sudo systemctl status autokom-platform-broker autokom-deployment-worker
```

**Attention** : le runtime `setup` attend un compte Linux `autokom` et un venv backend déjà installés. Il ne touche pas au backend principal `autokom-backend.service` ni aux zones BIND. Le setup n'installe pas automatiquement l'agent isolé Jenkins ou BuildKit : ils doivent être configurés et validés avant de permettre les builds clients.

Le pipeline **refuse** actuellement les build commands libres et Buildpacks : seules les constructions avec `Dockerfile` (AUTO/DOCKERFILE) sont prises en charge par le runner local sûr. `STATIC` nécessite aussi un Dockerfile; il ne faut pas annoncer un build universel qui n'existe pas.

## Domaines (API clients JWT habituelle)

- `GET /api/projects/<project_id>/environments/<env_id>/domains/` : lister les domaines du projet autorisé.
- `POST .../domains/create/` : `{"hostname":"app.exemple.org","domain_type":"CUSTOM"}` ou `SUBDOMAIN` pour un nom IKOM autorisé.
- `POST .../domains/<domain_id>/verify/` : vérification DNS + Certbot/proxy si la release locale est déjà prête. Sinon, état `VERIFYING` jusqu'au premier déploiement.

**CUSTOM** : enregistrer un TXT `_autokom-challenge.<domaine>` avec la valeur retournée et un A pointant vers `AUTOKOM_INGRESS_IPV4`. L'enregistrement TXT ne contient pas d'identifiant ni de secret de compte. La preuve est vérifiée réellement par le broker. Le propriétaire doit ouvrir lui-même son domaine chez son registrar/DNS.

**SUBDOMAIN** : AUTOKOM ne crée que dans `AUTOKOM_MANAGED_DOMAIN_SUFFIX` et via un jeton Cloudflare limité à la zone, et seulement après délégation effective. Aucun record existant différent n'est écrasé.

## Gestion et observabilité

`GET .../deployments/<run_id>/logs/` sert les lignes `AUTOKOM_LOG`/`AUTOKOM_STAGE` filtrées depuis Jenkins, `GET .../runtime-logs/` lit le Docker local via le broker sans accès aux secrets des autres environnements. `GET .../high-availability/` indique l'indisponibilité fonctionnelle tant que GCP n'est pas prêt.

Saisir dans l'interface les modes DevOps ou DevSecOps ; en DevSecOps, Semgrep et Trivy s'exécutent sur l'agent dédié avant le build. Ni le code ni les secrets ne transitent dans les arguments Jenkins, sauf l'UUID de run.

## Critères de validation avant ouverture aux utilisateurs

- Tester en premier avec un dépôt Dockerfile **fiable**, sans privilèges root, écoute sur l'adresse `0.0.0.0` interne, chemin healthcheck HTTP réactif.
- Vérifier que les images sont bien exportées en tar, chargées sans registre et que les ports sont uniquement sur `127.0.0.1`.
- Vérifier un renouvellement TLS de bout en bout via Certbot et un domaine DNS réellement délégué.
- Simuler un build invalide : le précédent conteneur ne doit pas être supprimé.
- Tester le rollback si Certbot échoue ; un rollback de plusieurs domaines doit être validé sur un environnement de staging avant d'envisager la production.
- Vérifier aucun accès de l'utilisateur web `autokom` à `/var/run/docker.sock`, `/etc/nginx/` et `/etc/letsencrypt/`.
