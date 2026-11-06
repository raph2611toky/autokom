# AUTOKOM — Correctif sandbox Jenkins (6 novembre 2026)

## Incident confirmé

Publication `e1c620ab`, build Jenkins `autokom-client-deploy #4` : l'agent `autokom-builder-01` tourne, `autokom-runner-key` est reconnu, et Django renvoie HTTP 200 au manifeste. Le contrôleur Jenkins bloque `new groovy.json.JsonSlurperClassic` lors du décodage du manifeste, avant le checkout Git. Cela n'implique ni une panne de Docker, ni une perte de la clé runner.

## Solution

Le helper Python AUTOKOM, déjà chargé de valider le manifeste avec les règles existantes, gagne une commande `metadata-line` qui produit exactement `owner/repo sha40` sur une ligne. Le Jenkinsfile approuvé valide ce contrat à l'aide d'une expression régulière ancrée avant de découper la chaîne. Le mode `metadata` JSON reste inchangé pour d'éventuels consommateurs existants.

Le parseur des logs reconnaît explicitement cette erreur Jenkins et renvoie une explication filtrée côté AUTOKOM. Il n'expose ni une trace Java/Groovy brute ni des variables d'environnement. Le sandbox reste actif ; aucun ajout de plugin ou de signature dans `Script Approval`.

## Kali Linux — depuis le commit ab5394526f

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline

git apply --check /home/raph/Downloads/AUTOKOM_Jenkins_Sandbox_Metadata_ab5394526f.patch
# Uniquement si la vérification précédente réussit :
git apply /home/raph/Downloads/AUTOKOM_Jenkins_Sandbox_Metadata_ab5394526f.patch
python3 -m unittest -v tests.test_autokom_groovy_sandbox
git diff --check
git diff --stat

git add scripts/runtime/Jenkinsfile.autokom-local \
  scripts/runtime/autokom_agent_local.py \
  platform/backend/apps/deployments/runs/console_stream.py \
  tests/test_autokom_groovy_sandbox.py \
  docs/AUTOKOM-JENKINS-SANDBOX-METADATA-2026-11-06.md

git commit -m 'fix: parse runner metadata without unsafe Groovy JSON'
git push origin main
```

**Ne pas utiliser** `git apply --reject`, `--3way`, ni `git add .` pour masquer un échec de patch. Si le commit a changé, reconstruire le diff depuis la nouvelle base avant de modifier AWS.

## Ubuntu AWS — sans configuration manuelle Jenkins

Ne pas exécuter pendant un build client actif.

```bash
sudo autokom update
sudo autokom runtime jenkins --check
sudo autokom runtime jenkins --apply
sudo autokom runtime doctor
sudo autokom runtime setup --check
sudo autokom runtime setup --apply
```

`runtime jenkins --apply` doit synchroniser le Jenkinsfile au contrôleur ; `runtime setup --apply` redémarre le worker qui charge le parseur des logs. Vérifier que le job contient bien `metadata-line` dans sa configuration. Il n'y a aucune modification frontend : inutile de reconstruire React, de changer Nginx, les credentials ou l'agent Jenkins.

## Vérification fonctionnelle

1. Créer **une nouvelle** publication manuelle de `portfolio`, puis consulter son journal de publication en mode Technique et la sortie admin Jenkins correspondante.
2. Vérifier que `JsonSlurperClassic` n'apparaît plus et que la phase de récupération Git débute.
3. Si une autre erreur survient (checkout, GitHub privé, BuildKit, artefact), elle doit être diagnostiquée à partir de son message réel, sans relancer automatiquement de multiples tentatives.
4. L'historique de l'exécution `e1c620ab` reste `FAILED` ; le correctif ne la transforme pas en succès et ne reconstitue pas les lignes filtrées lors de sa première ingestion.

Le patch a été testé hors ligne contre une reconstruction des fichiers du commit `ab5394526f`. Cette vérification ne constitue pas un test d'intégration Jenkins/AWS réel.
