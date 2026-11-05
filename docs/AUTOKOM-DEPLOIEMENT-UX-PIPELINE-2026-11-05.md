# AUTOKOM — Ajustements UX et synchronisation des déploiements

**Base du correctif :** code correspondant à `AUTOKOM_GitHub_Vite_Free_2026-11-05.patch`, appliqué après le correctif GitHub OAuth. La compatibilité doit toujours être vérifiée avec `git apply --check` sur Kali avant un commit.

## Portée

- La notification de la page Déployer est automatiquement effacée après **5 secondes** et conserve une marge inférieure supplémentaire de 24 px. Les détails d'erreurs métier restent disponibles dans les réponses API et l'historique des déploiements.
- Quand une seule installation GitHub est autorisée, la sélection est automatique, y compris si une ancienne installation enregistrée n'est plus valide.
- L'action de gestion GitHub comporte une icône de lien externe, un soulignement et un focus clavier visible ; le badge « Connecté » conserve une fonction d'état, pas d'action.
- Le navigateur de dossiers GitHub indique le chargement et refuse de sélectionner le dossier pendant le chargement.
- La dernière étape présente l'action **Déployer**. Le premier clic valide et prévisualise via Django. Après réussite, une action séparée **Confirmer et lancer Jenkins** apparaît : aucun déploiement n'est exécuté sans confirmation explicite.
- La détection du projet se fonde sur le dossier réel sélectionné et les métadonnées du dépôt GitHub, pas sur un brouillon obsolète. React/Vue/Svelte + Vite + `package-lock.json` avec script npm `build` sont reconnus comme `STATIC` (output `dist`, port `8080`). Ne pas choisir `dist` : sélectionner le dossier contenant `package.json`.
- Les étapes et les logs des jobs Django/Jenkins sont visibles sur la page de détail « Mes projets » avec une mise à jour automatique pendant les exécutions.
- Un échec terminal de Jenkins marque les étapes en cours comme échouées et les étapes non démarrées comme ignorées, sans simuler un succès.

## Ce que vérifie le backend

| Phase | Source réelle | Garantie apportée | À vérifier sur AWS |
|---|---|---|---|
| Offre Découverte | Django `check_entitlement()` | N'admet que les configurations STATIC | Plan actif attribué à l'environnement |
| Configuration | Django `check_configuration()` | Rejette modes non exécutables et commandes arbitraires | Contrat STATIC + Vite prêt |
| Source | GitHub App, checkout Jenkins | Dépôt et SHA autorisés | Installation et credential `autokom-github-app` |
| Build | `autokom_agent_local.py`, BuildKit rootless | Dockerfile contrôlé pour STATIC | Images et cache accessibles, artefact privé |
| File d'attente | MySQL + worker Django | Exécution idempotente, déclenchement hors requête HTTP | Worker, broker et Jenkins actifs |
| Publication | Broker Unix + Docker + Nginx | Healthcheck et activation après succès | Socket broker, DNS et certificat client |
| Suivi | `DeploymentRun.stages`, `DeploymentLogEntry` | Lecture des statuts et logs filtrés | Valeurs réelles pendant un job pilote |

Le correctif **ne confirme pas** à lui seul la bonne configuration de Jenkins, des credentials privés, du socket Unix, de BuildKit ni des certificats des applications clientes. Un véritable job de recette est nécessaire. Les images de base de la recette Vite sont encore référencées par tags ; leurs digests doivent être verrouillés avant un usage multi-client généralisé.

## Kali — application du patch

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline
git apply --check /home/raph/Downloads/AUTOKOM_Deploy_UX_Stages_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Deploy_UX_Stages_2026-11-05.patch
npm --prefix platform/frontend test
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_free_static_build
python3 -m compileall -q platform/backend/apps/deployments/runs platform/backend/apps/projects/configurations
git diff --check
git status --short
```

Après validation locale et revue du diff :

```bash
git add platform/frontend/src/components/LiveGitHubConnector.jsx \
  platform/frontend/src/pages/Deploy.jsx \
  platform/frontend/src/pages/LiveProjects.jsx \
  platform/frontend/src/services/projectWorkflow.js \
  platform/frontend/src/services/sourceAnalysis.js \
  platform/frontend/src/styles/deploy-integrations.css \
  platform/frontend/tests/liveDeploymentSafety.test.mjs \
  platform/backend/apps/deployments/runs/worker.py \
  platform/backend/apps/projects/configurations/services.py \
  docs/AUTOKOM-DEPLOIEMENT-UX-PIPELINE-2026-11-05.md
git commit -m "fix: synchronize Vite deploy readiness, GitHub UX and pipeline stages"
git push origin main
```

## Ubuntu AWS — mise en service

**Ne pas relancer** le setup Node/Nginx/Certbot : ces composants fonctionnent déjà. Déployer Django **avant** React.

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app status
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

### Recette pilote

1. Utiliser un dépôt personnel Vite avec `package.json`, `package-lock.json` et le script npm `build` ; sélectionner **le répertoire source** (`portfolio-app` dans le cas montré), puis attendre la détection « React/Vue/Svelte · Vite ».
2. Choisir l'offre Découverte / DevOps ; laisser les paramètres avancés vides. Sur la vérification, contrôler que la détection n'indique plus « Non déterminé ».
3. Cliquer sur **Déployer** pour la prévisualisation. Si une erreur 403 apparaît, lire son message : le backend précise maintenant le type de brouillon reçu ; ne jamais contourner les droits de l'offre en modifiant la base manuellement.
4. Après validation, cliquer sur **Confirmer et lancer Jenkins**, puis ouvrir **Mes projets → projet → Étapes et logs**. Vérifier dans l'ordre checkout GitHub, BuildKit, release Docker et publication Nginx/TLS.
5. Si un job échoue, vérifier les services et journaux via les commandes natives, sans partager de credentials :

```bash
sudo autokom runtime status
sudo autokom runtime jenkins --check
sudo systemctl status autokom-deployment-worker autokom-platform-broker jenkins --no-pager
sudo autokom app status
sudo journalctl -u autokom-deployment-worker -n 80 --no-pager
```

Le frontend ne remplace jamais un échec backend ou un état réel par un succès fictif. La connexion GitHub App, les plans payants et les autres moteurs de build restent séparés.
