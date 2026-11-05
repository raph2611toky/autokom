# AUTOKOM — Activation contrôlée de GitHub App en production

Version source cible : `9910e7ece1` (après intégration frontend « Mes projets » et « Déployer »). Ce changement n'altère aucun JSX de présentation/CSS. Ne pas activer de faux dépôts côté mode API.

## Défauts corrigés

- `autokom app frontend env edit VITE_AUTOKOM_GITHUB_ENABLED` refusait toujours `true`, erreur héritée du mode simulation.
- Le builder frontend rejetait indépendamment `true` avant Vite.
- La disponibilité GitHub côté navigateur devient **opt-in explicite** : uniquement `VITE_AUTOKOM_GITHUB_ENABLED=true`, `VITE_AUTOKOM_FRONTEND_ONLY=false` et `VITE_AUTOKOM_API_ENABLED=true`.
- `GET /api/projects/git/github/status/` renvoie `data.configured` (contrôle local sans test réseau), `data.connected` et conserve `data.installation_url`. Aucune clé ou token n'est renvoyé.
- Le composant réel explique si la GitHub App Django est incomplète et permet une nouvelle autorisation lorsque l'ancien token est refusé.
- Le service Django accepte désormais `AUTOKOM_RUNNER_SHARED_KEY` dans la liste de variables autorisées ; `autokom app backend env sync-runner-key` la copie de `runtime.env` vers `backend.env` sous root sans l'afficher. Si les deux clés existent et divergent, la commande refuse de les écraser.

## Ordre de déploiement

### Kali (source)

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline
git apply --check /home/raph/Downloads/AUTOKOM_GitHub_Live_9910e7_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_GitHub_Live_9910e7_2026-11-05.patch
PYTHONPATH=scripts/lib python3 -m unittest -v \
 tests.test_autokom_github_enablement tests.test_autokom_frontend_manager \
 tests.test_autokom_env_navigation tests.test_autokom_keyboard_menus \
 tests.test_autokom_env_domain_migration
npm --prefix platform/frontend test
git diff --check
git status --short
git add scripts/lib/autokom_cli.py scripts/lib/autokom_env_manager.py \
 scripts/lib/autokom_app_manager.py scripts/lib/autokom_frontend_manager.py \
 platform/frontend/src/config/runtime.js platform/frontend/src/config/githubApi.js \
 platform/frontend/src/components/LiveGitHubConnector.jsx \
 platform/backend/apps/projects/git/views.py \
 tests/test_autokom_github_enablement.py \
 docs/AUTOKOM-GITHUB-LIVE-RECETTE-2026-11-05.md
git commit -m 'fix: enable real GitHub App integration with backend checks'
git push origin main
```

### GitHub App Settings (action sur GitHub, une seule fois)

- App slug exact : `autokom-deploy`, et Client ID correspondant à celui configuré.
- Callback **projets** : `https://backend.auto.ikom.mg/api/projects/git/github/callback/`.
- Callback **connexion AUTOKOM** : `https://backend.auto.ikom.mg/api/auth/github/callback/` (préparer cette seconde URL même si les boutons de login OAuth restent inactifs dans le frontend actuel).
- URL webhook : `https://backend.auto.ikom.mg/api/projects/git/github/webhook/`; secret identique à `GITHUB_WEBHOOK_SECRET`. Souscrire à `push` si déploiements automatiques souhaités.
- Repository permissions : **Metadata: read** et **Contents: read** au minimum pour sélectionner/parcourir un dépôt. User permission **Email addresses: read** seulement pour la connexion/inscription par GitHub.
- Installer l'application dans le compte/organisation GitHub autorisé(e) et sélectionner les dépôts concernés. Les permissions modifiées peuvent demander une réapprobation de l'installation.
- Ne jamais copier le Client Secret ni les jetons dans ce rapport ou dans une capture d'écran.

### AWS Ubuntu (backend avant frontend)

```bash
sudo autokom update
sudo autokom app backend env show
sudo autokom runtime env show
sudo autokom app backend env sync-runner-key
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app status
sudo autokom app frontend env edit VITE_AUTOKOM_GITHUB_ENABLED
# choisir true (VITE_AUTOKOM_API_ENABLED=true et FRONTEND_ONLY=false doivent déjà être présents)
sudo autokom app frontend env show
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

Aucun `frontend setup`, Certbot, Nginx ni Node.js ne doit être réinstallé à cette étape. Les commandes `env ...` sensibles doivent conserver la protection par passphrase AUTOKOM.

### Recette navigateur (avec session AUTOKOM authentifiée)

1. Ouvrir `https://auto.ikom.mg/deploy`, Network Firefox et vérifier que l'appel `GET /api/projects/git/github/status/` répond **200**.
2. Dans la console Firefox, afficher seulement les booléens, jamais les cookies :

```js
fetch('/api/projects/git/github/status/', { credentials: 'same-origin' })
 .then(async r => { const x = await r.json(); console.log({ http: r.status, configured: x?.data?.configured, connected: x?.data?.connected }) })
```

3. `configured: true` signifie uniquement que les variables GitHub Django sont présentes. **Ce n'est pas une preuve que GitHub acceptera la connexion.**
4. Si `connected: false`, cliquer **Connecter GitHub**, autoriser sur GitHub, puis vérifier la redirection vers `/integrations/github?status=connected` et le retour à `/deploy`.
5. Sélectionner une installation : `GET /api/projects/git/github/installations/` doit renvoyer `200` et une liste, pas forcément non vide. Ensuite vérifier les endpoints `repositories`, `branches`, `directories` avec les vrais IDs reçus.
6. Si le statut reste `connected: false`, vérifier dans l'onglet Network le `POST /api/projects/git/github/start/` et la redirection callback, puis consulter les logs Django (sans tokens OAuth).

**Lecture des erreurs :** HTTP 401 = session AUTOKOM absente/expirée ; HTTP 403 = permission GitHub ou permission AUTOKOM insuffisante ; HTTP 409 = reconnecter GitHub ; HTTP 503 = GitHub inaccessible / configuration non opérationnelle. Le callback peut renvoyer `status=error` ; vérifier en priorité l'URL enregistrée et les permissions.

### Vérification Jenkins : indépendante de l'OAuth GitHub

```bash
sudo autokom runtime status
sudo autokom runtime jenkins --check
sudo systemctl status autokom-platform-broker autokom-deployment-worker jenkins --no-pager
```

Pour le **premier déploiement test**, vérifier avant le lancement :

- Le job Jenkins approuvé `autokom-client-deploy` est opérationnel.
- L'agent label `autokom-isolated` est disponible et BuildKit rootless fonctionne sans socket Docker root.
- Jenkins possède les credentials `autokom-runner-key` (même valeur que `AUTOKOM_RUNNER_SHARED_KEY`) et `autokom-github-app` (lecture du dépôt privé). La connexion OAuth du navigateur ne crée **pas** ce credential Jenkins.
- Le runner atteint la vraie URL HTTPS `https://backend.auto.ikom.mg` et l'endpoint manifeste HMAC autorisé. Aucun credential ne doit être injecté dans le JavaScript React.
- Le dépôt test possède un Dockerfile autorisé ; les modes Buildpacks universels et HA ne sont pas encore exécutables.
- L'application de test ne doit pas avoir de données de production avant validation du confinement (réseau, CPU/RAM, artefacts, rollback).

## Limitations restant à traiter

- Les boutons « Se connecter/S'inscrire avec GitHub » sont des **placeholders frontend** ; le backend possède un autre OAuth (`/api/auth/github/...`), mais le callback React `/auth/github/callback` n'est pas encore câblé. Ne pas les annoncer fonctionnels.
- Le statut local `configured/connected` n'effectue pas une requête à GitHub ; une autorisation déjà révoquée peut apparaître dans la DB jusqu'à la prochaine requête API, laquelle exige une reconnexion.
- L'authentification GitHub, les webhooks, les builds Jenkins et la publication HTTPS clients doivent être testés sur AWS avec un dépôt d'essai : les tests unitaires hors réseau ne suffisent pas.
- Les modes payants/DevSecOps nécessitent un entitlement actif, l'auto-deploy nécessite un webhook valide, et HA reste une fonctionnalité non activée.
