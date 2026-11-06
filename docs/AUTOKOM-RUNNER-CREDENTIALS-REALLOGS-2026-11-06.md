# AUTOKOM — Correction runner Jenkins + journaux synchronisés

## Diagnostic prouvé

Le build Jenkins #3 reçoit `AUTOKOM_STAGE:source:RUNNING` puis échoue sur `Could not find credentials entry with ID 'autokom-runner-key'`. L'agent `autokom-builder-01` est connecté et les endpoints Django répondent. L'erreur n'est pas un incident du socket Unix.

## Correction

`sudo autokom runtime jenkins --check` teste en lecture seule le credential Jenkins. `--apply` crée le credential manquant comme Secret text Jenkins GLOBAL. La clé est lue depuis `AUTOKOM_RUNNER_SHARED_KEY` dans `/etc/autokom/secrets/runtime.env` en mémoire seulement, jamais dans argv, stdout, le dépôt ou les logs. Ne pas afficher la clé. Après création, un GET vérifie sa présence. Si l'ID est déjà utilisé, AUTOKOM ne le remplace pas : la description et le fingerprint doivent correspondre au secret runtime. L'existence du fingerprint n'est pas une attestation cryptographique de la valeur stockée dans Jenkins ; une corruption ou modification manuelle de la valeur dans Jenkins impose une rotation contrôlée.

Une commande Jenkins dont le secret n'est pas présent provoquait une erreur même lorsque `autokom runtime doctor` était au vert. Le doctor vérifie désormais également le credential runner.

Le pipeline teste une lecture Git sans authentification pour les dépôts publics ; il ne supprime pas le contrôle strict du commit SHA. Pour les dépôts privés, l'ID distinct `autokom-github-app` reste nécessaire. La solution de production future consiste à utiliser un jeton court terme dérivé de l'installation GitHub App pour chaque projet, avec accès minimal au dépôt.

La vue Technique réutilise le journal `/logs/?mode=technical`, qui est le superset de `simple`, trie par `sequence`, affiche les transitions de stage réelles et conserve toutes les lignes de synthèse avec le diagnostic autorisé. Le filtre par étape suit la séquence des marqueurs `AUTOKOM_STAGE`.

## Installation Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_RunnerCredential_RealLogs_2026-11-06.patch
git apply /home/raph/Downloads/AUTOKOM_RunnerCredential_RealLogs_2026-11-06.patch
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_jenkins_credentials
npm --prefix platform/frontend test
git diff --check
git add scripts/lib/autokom_jenkins_credentials.py scripts/lib/autokom_runtime_admin.py scripts/runtime/Jenkinsfile.autokom-local platform/backend/apps/deployments/runs/worker.py platform/frontend/src/pages/LiveRuns.jsx platform/frontend/src/services/deploymentLogTimeline.js platform/frontend/tests/deploymentLogTimeline.test.mjs tests/test_autokom_jenkins_credentials.py docs/AUTOKOM-RUNNER-CREDENTIALS-REALLOGS-2026-11-06.md
git commit -m 'fix: auto-provision Jenkins runner key and synchronize technical logs'
git push origin main
```

## AWS — aucun secret copié manuellement

```bash
sudo autokom update
sudo autokom runtime jenkins --check
sudo autokom runtime jenkins --apply
sudo autokom runtime doctor
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom runtime setup --check
# Si aucune publication n'est active :
sudo autokom runtime setup --apply
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
```

La mise à jour du worker exige `runtime setup --apply`. Elle ne modifie ni le socket Docker public ni les vhosts des autres sites. Ne pas lancer `runtime setup --apply` pendant un build actif.

## Recette

1. Dans le doctor, vérifier le credential runner sans exposer sa valeur.
2. Tester un nouveau déploiement manuel avec `portfolio-app`, public, et suivre la console autorisée dans AUTOKOM.
3. Les journaux techniques doivent inclure tous les événements disponibles en Synthèse, enrichis des étapes et diagnostics autorisés. Ils ne sont pas un dump brut de tous les stdout clients.
4. Vérifier que le build passe la phase source. Si la phase suivante échoue, inspecter les nouvelles erreurs : permissions Git, BuildKit rootless, manifest/artefact, Nginx, etc.
5. Une rotation du secret partagé doit être coordonnée entre runtime.env, backend.env et Jenkins. Ne jamais exposer ou coller le secret du nœud Jenkins dans un ticket public.
