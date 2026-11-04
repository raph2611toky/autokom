# AUTOKOM Platform — Déploiement client réel (phase backend)

## Objectif et périmètre

Ce correctif porte **exclusivement sur `platform/backend/`**. Il **ne remplace pas** les APIs déjà présentes dans `apps/projects/{project,git,configurations,environments,secrets}` ni l'authentification e-mail/GitHub des clients. Il ajoute le moteur qui manquait : `apps/deployments/runs` (API, file MySQL, worker, dispatch/poll Jenkins, logs) et la demande commerciale HA (`apps/plans/entitlements`).

**Important :** le backend peut recevoir et mettre en file des demandes une fois configuré, mais un déploiement de code client ne fonctionne réellement qu'après préparation d'un **agent de build isolé**, d'un **registre privé**, des **credentials Jenkins** et d'un **cluster Kubernetes** avec permissions strictes. Aucun job dangereux n'est exécuté par Gunicorn, dans les requêtes Django ou dans le Core `autokom`. Ne pas activer de job Jenkins sur le même hôte que les secrets et le backend de production.

## Parcours fonctionnel

1. Authentification client : JWT AUTOKOM existant, éventuellement obtenu via GitHub OAuth.
2. `POST /api/projects/git/github/start/` : autorisation GitHub App **projets**, distincte de la connexion GitHub de l'utilisateur.
3. `GET /api/projects/git/github/installations/` puis `GET .../repositories/` et `.../branches/` : dépôts/branches réellement autorisés, y compris privés.
4. Création d'un projet (nom, dépôt et installation). Un environnement `production` et une configuration brouillon sont déjà créés.
5. Configuration versionnée : branche, répertoire racine, Dockerfile ou buildpacks, commandes de build/démarrage, port, chemin de santé, DevOps/DevSecOps, plan, redéploiement automatique.
6. Variables et secrets via les APIs existantes (`EnvironmentVariable`, `SecretReference`). **Jamais** de mot de passe dans `DeploymentConfig.settings` ou dans les paramètres Jenkins.
7. Finalisation (vérifie branche GitHub, quotas et entitlement côté serveur).
8. Prévisualisation puis `launch` : résolution de la SHA GitHub autorisée, snapshot des quotas, idempotency key, une exécution active par environnement.
9. Worker systemd séparé : récupère les jobs MySQL, appelle **uniquement** le job Jenkins approuvé avec `AUTOKOM_RUN_ID` (pas de jeton GitHub ni de script client).
10. Pipeline approuvé : checkout privé par GitHub App Jenkins, scan Semgrep/Trivy en mode DevSecOps, image OCI par BuildKit rootless ou Buildpacks sur **agent isolé**, push registre privé, déploiement namespace Kubernetes, readiness probe et journal filtré.
11. Backend suit la file Jenkins / les étapes et enregistre `Release` et `HostedApplication` **après le succès réel du job**. Consultation des logs par curseur, annulation en file ou en cours.
12. `push` signé par GitHub : si la branche correspond et `auto_deploy=True`, un déploiement est mis en file (pas d'exécution réseau dans le webhook). Livraison unique dédupliquée.

## Routes ajoutées

Toutes les routes client utilisent `Authorization: Bearer <JWT_CLIENT>` et vérifient l'accès organisation/projet/environnement. Format de réponse inchangé : `{ "success": true, "message": "...", "data": {...}, "errors": null }`.

| Méthode | Chemin sous `/api/projects/` | Résultat |
|---|---|---|
| GET | `{project_id}/environments/{environment_id}/deployments/preview/` | Vérification préliminaire config/offre/ressources |
| POST | `.../deployments/launch/` | File Jenkins (202). En-tête optionnel `Idempotency-Key` |
| GET | `.../deployments/` | Historique paginé |
| GET | `.../deployments/{run_id}/` | État, stages, SHA, ressources réellement autorisées |
| GET | `.../deployments/{run_id}/logs/?after=0` | Jusqu'à 100 événements filtrés, curseur pour logs live |
| POST | `.../deployments/{run_id}/cancel/` | Demande d'annulation vérifiée, traitée par le worker |
| GET | `.../high-availability/` | HA non demandée, pending, active, expired |
| POST | `.../high-availability/request/` | Demande de 2 à 20 réplicas **en attente de paiement et validation** |
| GET | `.../usage/` | Quota autorisé + consommation CPU/RAM mesurée par Prometheus (null si aucune série) |
| GET | `.../runtime-logs/` | Loki optionnel, isolé par environnement, pas de requête LogQL client |

Un endpoint **interne** existe : `GET /api/internal/runner/deployments/{run_id}/manifest/` avec HMAC serveur et horodatage à durée courte. Il est **exclu de Swagger client** et ne renvoie jamais de secret au frontend. Le runner doit appeler via HTTPS avec `X-Autokom-Runner-Time` et `X-Autokom-Runner-Signature` (HMAC-SHA256 sur `autokom-runner:v1:<timestamp>:<run_id>`). 

## Options payantes — sans fausse activation

- Les droits `ApplicationEntitlement` existants autorisent le mode DevOps gratuit ou DevSecOps selon offre réellement accordée. `ResourcePlan` indique CPU/RAM/stockage par instance, plus `max_instances`.
- Une demande de haute disponibilité crée `HighAvailabilityOption(status=PENDING)`. La demande n'encaisse **aucun paiement** et ne crée **aucun réplica supplémentaire**. Tant qu'elle est `PENDING`, les déploiements standard à un réplica restent disponibles, et le statut de la demande apparaît dans l'API HA.
- Seuls l'OWNER ou un ADMIN d'organisation demandent cette option. Activation seulement dans Django Admin après validation contractuelle/financière : administrateur approbateur, `billing_reference`, `activated_at` et durée optionnelle. Aucun webhook de paiement n'est supposé.
- Pour demander 2+ réplicas actifs, il faut simultanément : option active et non expirée, approbation + facture confirmée, plan avec `max_instances` suffisant, `AUTOKOM_HA_CLUSTER_READY=True` **uniquement après tests réels d'un cluster multi-nœuds**, et RBAC/répartiteur configurés. Sans cela, l'API renvoie 409/403 et ne dégrade **jamais silencieusement** une option HA vers un seul réplica.
- Le pipeline impose une anti-affinité Kubernetes entre machines pour 2+ réplicas et une stratégie rolling-update sans indisponibilité volontaire. **Cela ne suffit pas à garantir la HA** sans charge balancée, stockage/DB redondants, plusieurs zones et supervision.

## Réglages serveur à ajouter (jamais dans Git)

Conserver `/etc/autokom/platform-backend.env` root:0600. Ne pas copier de vraies clés en conversation ni dans les archives.

```dotenv
AUTOKOM_JENKINS_URL=http://127.0.0.1:8080
AUTOKOM_JENKINS_JOB=autokom-client-deploy
AUTOKOM_JENKINS_USER=<jenkins-service-account>
AUTOKOM_JENKINS_API_TOKEN=<jenkins-api-token>
AUTOKOM_RUNNER_SHARED_KEY=<32+caracteres_aleatoires_generes_securitairement>
AUTOKOM_IMAGE_REGISTRY=registry.example.invalid/autokom
AUTOKOM_HA_CLUSTER_READY=False
AUTOKOM_PROMETHEUS_URL=http://127.0.0.1:9090
AUTOKOM_LOKI_URL=http://127.0.0.1:3100
AUTOKOM_RUNTIME_LOGS_ENABLED=False
```

La connexion Jenkins HTTP est acceptée **uniquement sur loopback** ; utiliser HTTPS TLS vérifié pour un hôte distant. L'API token Jenkins est une identité de service minimalement habilitée au job approuvé. Le token HMAC runner doit aussi être enregistré comme secret Jenkins (ID `autokom-runner-key`). Sur le runner, `AUTOKOM_API_BASE` est l'URL HTTPS réelle du backend ; `AUTOKOM_IMAGE_REGISTRY` doit coïncider avec celle du backend.

**Attention :** `autokom app setup` maintient sa propre liste d'options acceptées. Cette étape backend seule ne modifie pas le CLI : vérifier que ces nouveaux réglages restent présents dans le fichier service au prochain `setup`. Ne jamais remplacer un `.env` existant par un exemple.

## Déploiement du backend mis à jour

Sur Kali depuis la racine du dépôt AUTOKOM :

```bash
unzip -o ~/Downloads/AUTOKOM_Deploy_Clients_Backend_PLATFORM_ONLY.zip
python3 -m unittest discover -s platform/backend/tests -p test_deployment_pipeline_security.py -v
python3 -m compileall -q platform/backend/apps/deployments/runs platform/backend/apps/plans/entitlements
# Avec l'environnement de développement Django/MySQL configuré :
cd platform/backend
python manage.py check
python manage.py makemigrations --check --dry-run
cd ../..
git add platform/backend/
git diff --cached --check
git diff --cached --name-status
git commit -m 'feat: client deployment orchestration, Jenkins runner, logs, HA entitlement'
git push origin main
```

Sur Ubuntu :

```bash
cd /opt/autokom/source
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app status
```

**Ne pas exécuter `manage.py makemigrations` ni `--fake` en production.** AUTOKOM doit appliquer les migrations versionnées au déploiement comme précédemment.

## Jenkins, agent isolé et Kubernetes (prérequis distincts)

1. Provisionner un **agent Jenkins dédié, éphémère et isolé**, label `autokom-isolated`, **distinct du serveur backend**. L'agent dispose de `python3`, Git, Semgrep local avec règles de sécurité, Trivy, `buildctl` rootless ou `pack`, kubectl et des credentials registry. Ne pas donner au client accès au socket Docker hôte ni aux identifiants Jenkins.
2. Configurer des identifiants Jenkins : `autokom-github-app` (GitHub App avec permission Repository contents: read et installation sur les repos clients autorisés) et `autokom-runner-key` (secret texte HMAC). Le job ne charge **jamais** de Jenkinsfile provenant du dépôt client.
3. Installer sur l'agent **hors du répertoire Git client** : `deployment_templates/autokom_agent.py` vers `/opt/autokom-runner/autokom_agent.py` (fichier possédé par l'administrateur). Règles Semgrep locales à `/etc/autokom-runner/semgrep.yml` ou `AUTOKOM_SEMGREP_RULES` ; interdiction d'enregistrer des tokens dans ce fichier.
4. Provisionner le cluster Kubernetes, registry privé, imagePullSecrets, ingress controller, cert-manager et namespaces **au préalable**, avec RBAC par namespace et `automountServiceAccountToken: false` pour les pods clients. Le namespace attendu est `autokom-` + les 20 premiers caractères hexadécimaux de l'UUID d'environnement. **Ne pas accorder `cluster-admin` à un runner partagé.** Préférer un agent / SA par locataire et des politiques réseau/PSA strictes.
5. Si un domaine est déjà `ACTIVE` après validation DNS, l'agent crée l'Ingress TLS avec `AUTOKOM_CLUSTER_ISSUER` (cert-manager) et `AUTOKOM_INGRESS_CLASS` (par défaut `nginx`). Sans DNS valide, aucune disponibilité publique n'est promise.
6. Créer le job unique `autokom-client-deploy` depuis **le Jenkinsfile versionné côté backend**, avec `python manage.py autokom_jenkins_setup --apply` exécuté via l'environnement service; `--apply` **refuse de remplacer** un job déjà existant. La première exécution Jenkins doit pouvoir utiliser le paramètre `AUTOKOM_RUN_ID`.
7. Installer `deployment_templates/autokom-deployment-worker.service.example` comme service dédié `autokom-deployment-worker.service`, puis activer avec systemd. En l'absence de worker, les déploiements restent en file `QUEUED`.
8. Préparer un dépôt de démonstration **sans secrets** dans une branche de test ; vérifier les étapes GitHub → SAST/Trivy → build OCI → registry → Kubernetes → readiness → état API; tester les erreurs, la déduplication et l'annulation. Passer ensuite seulement au multi-tenant réel.

Ce correctif ne modifie volontairement pas `scripts/lib/autokom_cli.py` : le raccordement de la gestion du worker/agent aux commandes `autokom app ...` devra être fait dans un patch **Core** séparé, après stabilisation des API. 

## Modes de build / limites explicites

- `DOCKERFILE` : Dockerfile dans le chemin source ; **les commandes de build doivent être dans le Dockerfile**, aucune exécution shell sur le backend. BuildKit doit être en mode isolé/rootless.
- `AUTO` : Dockerfile présent → BuildKit, sinon buildpacks Paketo ; `BUILDPACKS` → `pack build --publish`. Pour Node, seules les commandes `npm run <script>`, `yarn <script>` ou `pnpm run <script>` sont converties à `BP_NODE_RUN_SCRIPTS` ; une autre commande personnalisée produit une erreur explicite au lieu d'être exécutée sur l'hôte.
- `STATIC` : le répertoire `output_directory` doit contenir les fichiers déjà construits. Le runner utilise un Dockerfile Nginx de confiance. Build statique personnalisé non encore pris en charge : prévoir un builder isolé additionnel avant de l'exposer au frontend.
- `start_command` : commande interne au conteneur du client, jamais exécutée sur le serveur backend ni sur l'agent hôte.
- DevOps : checkout, build isolé, déploiement, health ; DevSecOps : + scans Semgrep/Trivy **bloquants** et scan d'image après build.

## Logs et sécurité

- Les endpoints publics exposent uniquement les lignes structurées `AUTOKOM_STAGE:` / `AUTOKOM_LOG:` et passent un filtre de masquage ; **pas** la console Jenkins complète qui pourrait contenir des secrets. API logs via curseur `after`, utile pour un affichage live.
- Ressources : quotas issus du plan, consommation réelle provenant uniquement de séries Prometheus Kubernetes (`container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`) ; valeurs null si absentes, jamais inventées.
- Logs runtime séparés dans Loki, désactivés par défaut, activables uniquement après configuration Alloy qui pose le label `autokom_environment=<uuid>`. Aucune requête LogQL arbitraire n'est acceptée depuis le client.
- Les variables chiffrées existantes ne quittent le backend que par l'API runner HTTPS HMAC, au moment d'un run `RUNNING`. Le job ne reçoit que l'UUID du run. Le runner écrit les valeurs dans les secrets Kubernetes par **stdin** (jamais dans la ligne de commande). Le cluster doit chiffrer les secrets **au repos** et restreindre l'accès RBAC. Le runner HMAC partagé est un secret de haute importance : préférer mTLS et cloisonnement réseau, puis des jetons éphémères limités à un run lors d'un prochain durcissement.
- Aucun `subprocess` de client n'est lancé dans les vues Django. Les sous-processus du runner opèrent sur un hôte isolé et utilisent une liste d'arguments, sans `shell=True` ; les secrets sont retirés de l'environnement transmis au constructeur d'image.
- Les modifications de déploiement, les logs et les demandes HA exigent une appartenance vérifiée au projet ; seuls OWNER/ADMIN peuvent demander un service payant.

## Vérifications livrées

- 12 tests statiques/stdlib (`tests/test_deployment_pipeline_security.py`) sur la signature HMAC, confidentialité, Kubernetes, isolation, chemin source, Swagger, migrations et APIView.
- 5 tests Django `SimpleTestCase` (`apps/deployments/runs/tests.py`) à lancer sur un environnement de test disposant de Django/DRF.
- **Non testés dans cet environnement de génération :** migrations Django réelles et MySQL, fonctionnement du job Jenkins, credentials de l'agent, push d'images et rollout Kubernetes. Ces points sont **des étapes obligatoires**, pas des succès présumés.
