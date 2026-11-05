# AUTOKOM — Agent Jenkins isolé, file d'attente et suivi des publications

## Diagnostic établi sur AWS

`Still waiting to schedule task: Jenkins doesn't have label autokom-isolated` veut dire que le contrôleur ne possède **aucun agent disponible ayant ce label**. Le job `autokom-client-deploy` est bien synchronisé ; la communication Django → broker → Jenkins est opérationnelle.

Un job Jenkins présent dans le tableau de bord **ne signifie pas** que son exécution peut être planifiée. La Stage View reste vide tant que la première étape ne commence pas.

## Inclus dans ce correctif

- `autokom runtime doctor` inspecte via l'API Jenkins le label, l'état en ligne et le nombre d'exécuteurs d'un **agent distinct du contrôleur**.
- Le broker vérifie le même état **avant** d'accepter une nouvelle mise en file. Refus explicite si aucun agent isolé n'est disponible ; jamais de secours sur le contrôleur.
- Le worker annule les éléments de file inchangés après 8 minutes et marque la publication bloquée. Il ne relance pas automatiquement une requête potentiellement déjà acceptée.
- `autokom runtime jenkins --apply` crée au mieux une List View `AUTOKOM-Clients` si elle n'existe pas, en laissant les autres jobs et vues intacts. Cela ne crée pas de nouveaux jobs par client : le job mutualisé sécurise l'exécution de chaque projet.
- Les cartes de publication ont des états colorés (jaune/en cours, vert/terminé, rouge/échec), une progression en étapes lisibles et des messages clients filtrés.
- Les textes et le design existants sont préservés.

## IMPORTANT — Agent d'exécution encore à provisionner

Ce correctif **ne déploie pas automatiquement** un service d'agent Java ni BuildKit rootless. L'automatisation sûre de cette partie nécessite également la gestion de son secret d'enrôlement Jenkins, de BuildKit rootless, des credentials GitHub et du réseau de build. Ne pas prétendre qu'un simple label attribué au contrôleur constituerait cette isolation.

Dans Jenkins, sous *Manage Jenkins → Nodes → New Node*, créer un **Permanent Agent** nommé `autokom-isolated` avec :

- Nombre d'exécuteurs : `1`
- Répertoire de travail : `/var/lib/autokom-jenkins-agent` (sur une identité Unix distincte et non privilégiée)
- Labels : `autokom-isolated`
- Usage : *Only build jobs with label expressions matching this node*
- Méthode de lancement : *Launch agent by connecting it to the controller* (WebSocket recommandé si tout est sur la même machine)

Ce nœud doit être connecté et en ligne. Ne pas lier ce label au nœud « Built-In Node » / contrôleur, et ne pas ajouter le compte de build au groupe `docker`.

Prérequis supplémentaires à contrôler **sous le compte de build**, sans désactiver les mécanismes DevSecOps :

- `java`, `git`, `python3`, `semgrep`, `trivy`, `buildctl` et daemon BuildKit **rootless** ;
- accès en lecture au runner approuvé et à son dossier de travail ;
- `AUTOKOM_API_BASE` disponible sur l'agent, pointant vers le backend HTTPS ;
- credentials Jenkins `autokom-runner-key` et `autokom-github-app` de types adaptés et attribués au job ;
- `/var/lib/autokom/build-artifacts` accessible au compte de build selon le principe du moindre privilège ;
- nœud Jenkins ONLINE dans `Manage Jenkins → Nodes`.

Après la mise en ligne de l'agent, appeler `sudo autokom runtime doctor` puis lancer **une seule** publication test.

## Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Jenkins_Agent_Preflight_Progress_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Jenkins_Agent_Preflight_Progress_2026-11-05.patch
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_agent_queue_and_views tests.test_autokom_runtime_broker tests.test_autokom_runtime_deployment_hardening
npm --prefix platform/frontend test
git diff --check
git add scripts/lib/autokom_platform_broker.py scripts/lib/autokom_runtime_admin.py platform/backend/apps/deployments/runs/worker.py platform/frontend/src/pages/LiveProjects.jsx platform/frontend/src/services/deploymentPresentation.js platform/frontend/src/styles/deploy-integrations.css platform/frontend/tests/deploymentPresentation.test.mjs tests/test_autokom_agent_queue_and_views.py docs/AUTOKOM-JENKINS-AGENT-UI-SUIVI-2026-11-05.md
git commit -m "fix: reject unavailable Jenkins agents and clarify client deployment progress"
git push origin main
```

## Ubuntu après validation Kali

```bash
sudo autokom update
sudo autokom runtime doctor
sudo autokom runtime jenkins --apply
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
```

Le doctor **échouera tant que l'agent n'aura pas été installé et connecté** : c'est un garde-fou, pas un nouveau bug. Les déploiements clients déjà présents ne doivent pas être relancés en double. Vérifier leur état après synchronisation.

## Limites de validation

Tests unitaires Python et JavaScript exécutés localement. Aucune instance Jenkins ou AWS n'était disponible ici pour tester la création de List View, l'agent WebSocket et les credentials. `autokom runtime jenkins --apply` ne doit pas écraser les jobs existants hors AUTOKOM.
