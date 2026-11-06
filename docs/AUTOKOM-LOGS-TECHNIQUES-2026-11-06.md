# AUTOKOM — Deux modes de journal de publication (06/11/2026)

## Constats

Pour la publication `9fc2d8de`, Django répond `202` au lancement et `200` aux consultations du suivi. La publication termine `FAILED` sur l’étape source, mais les trois lignes de suivi conservées ne contiennent pas la véritable exception Jenkins. Les requêtes GitHub webhook `403` relevées dans les journaux backend constituent un diagnostic indépendant et ne prouvent pas la cause du déploiement manuel.

## Changement

- Mode **Technique** sélectionné par défaut, style console lisible, filtrable par étape; mode **Synthèse** séparé. Les deux interrogent les mêmes API Django authentifiées et contrôlées par environnement.
- Les sorties de console Jenkins sont ingérées par le worker de manière incrémentale et idempotente. Les lignes admises sont limitées à une **liste positive** : orchestrateur de pipeline, Git, npm/Vite, BuildKit et erreurs communes. Les sorties applicatives libres, secrets, adresses de connexion privées et contenus non reconnus ne sont **pas** reproduits.
- Les entrées techniques sont stockées dans `DeploymentLogEntry` avec `level=TECH`, préfixées par l’étape; aucune migration requise. Les marqueurs `AUTOKOM_STAGE` existants restent informatifs, jamais une autorisation de publication. La capacité par run reste bornée à 10 000 entrées et chaque ligne à 800 caractères.
- `GET /api/projects/<project>/environments/<env>/deployments/<run>/logs/?after=0&mode=technical` renvoie la synthèse et les diagnostics filtrés. `mode=simple` retire les diagnostics. Tout autre mode est refusé. Seuls les membres autorisés à l’environnement peuvent consulter les journaux.
- Les logs **déjà perdus** au filtrage avant cette mise à jour ne peuvent pas être reconstruits : ouvrir le bon build dans Jenkins et consulter `Console Output`, puis masquer les secrets avant tout partage.

## Tests Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Logs_Technique_Synthese_2026-11-06.patch
git apply /home/raph/Downloads/AUTOKOM_Logs_Technique_Synthese_2026-11-06.patch
python3 platform/backend/tests/test_deployment_worker_consistency.py
python3 platform/backend/tests/test_deployment_technical_logs.py
npm --prefix platform/frontend test
git diff --check
git add platform/backend/apps/deployments/runs/console_stream.py platform/backend/apps/deployments/runs/worker.py platform/backend/apps/deployments/runs/serializers.py platform/backend/apps/deployments/runs/views.py platform/backend/apps/deployments/runs/swagger/run_logs.py platform/backend/tests/test_deployment_technical_logs.py platform/frontend/src/pages/LiveRuns.jsx platform/frontend/src/services/deploymentsApi.js platform/frontend/src/styles/deploy-integrations.css platform/frontend/tests/technicalLogs.test.mjs scripts/runtime/Jenkinsfile.autokom-local docs/AUTOKOM-LOGS-TECHNIQUES-2026-11-06.md
git commit -m 'feat: two safe per-stage deployment log modes'
git push origin main
```

## Ubuntu AWS

Les deux composants applicatifs doivent être publiés car les vues, le worker et le frontend changent. Le job Jenkins doit également être synchronisé puisque ses marqueurs de stage sont renforcés.

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom runtime setup --check
# seulement si aucune publication active :
sudo autokom runtime setup --apply
sudo autokom runtime jenkins --check
sudo autokom runtime jenkins --apply
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom runtime doctor
```

Ne modifier ni les fichiers de secrets ni les droits Docker. Le service worker est réinstallé/rechargé par `runtime setup --apply`; vérifier `systemctl show -p MainPID,ActiveEnterTimestamp autokom-deployment-worker` si les nouvelles lignes n’apparaissent pas. Un build client peut encore échouer pour des raisons indépendantes (credentials GitHub, checkout, manifeste, buildkit). Ne pas retenter automatiquement les déploiements.

## Recette

Vérifier deux nouveaux déploiements de test, un réussi et un volontairement refusé. Dans le détail du run, tester le mode Technique par défaut, Synthèse, le filtre par étape, l’actualisation et le chargement par curseur. Vérifier l’absence de tokens et secrets. Comparer les messages de statut avec la console Jenkins admin et s’assurer que les étapes ne sont jamais déclarées réussies sur la seule base de texte client.
