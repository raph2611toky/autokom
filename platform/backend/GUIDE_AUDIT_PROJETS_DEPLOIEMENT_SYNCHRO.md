# AUTOKOM Platform — Audit de cohérence projets, déploiement et logs

Ce correctif modifie **uniquement** `platform/backend/`.

## Corrections appliquées

1. **Synchronisation Jenkins → MySQL** : stockage atomique des offsets progressifs et des lignes partielles, prise en charge des logs multi-chunks (y compris après la fin du build), et verrouillage de la séquence des logs par déploiement.
2. **Docker ↔ MySQL** : l'activation de `Release` n'est plus enregistrée avant la confirmation `runtime.commit` du broker. Si le broker échoue, rollback demandé et statut `FAILED`. Si MySQL échoue **après** le commit Docker, statut `BLOCKED` (réconciliation opérateur), sans rollback aveugle d'une version déjà validée.
3. **Jenkins temporairement indisponible** : le worker poursuit ses tentatives aux cycles suivants, sans faire échouer toutes les exécutions en cours à cause d'une erreur de socket unique.
4. **Transparence API** : le champ `error_message` (raison générique) apparaît dans les sérialiseurs des déploiements. Aucun token, secret ni variable privée ne doit être mis dans ce champ.
5. **Compatibilité du builder** : seules les configurations `DOCKERFILE` sans commandes personnalisées peuvent être finalisées. `AUTO`, `BUILDPACKS` et `STATIC` restent modifiables en brouillon, mais sont **non opérationnels** sur le runner fourni. Les chemins de healthcheck doivent respecter les validations du broker.

**Aucune migration SQL**, aucun changement de schéma, aucun fichier `.env`.

## Prérequis contrôlables sur AWS (lecture seule)

```bash
cd /opt/autokom/source
sudo autokom app status
sudo autokom runtime status
sudo autokom runtime jenkins --check
sudo systemctl is-active autokom-backend autokom-deployment-worker autokom-platform-broker jenkins docker nginx
sudo systemctl is-enabled autokom-deployment-worker autokom-platform-broker
sudo systemctl show -p EnvironmentFiles autokom-deployment-worker autokom-platform-broker
sudo test -S /run/autokom/platform-control.sock && echo 'Socket AUTOKOM présent'
sudo stat -c '%a %U:%G %n' /run/autokom/platform-control.sock /var/lib/autokom/build-artifacts
sudo journalctl -u autokom-deployment-worker -n 40 --no-pager
sudo journalctl -u autokom-platform-broker -n 40 --no-pager
```

- Le healthcheck HTTP du backend confirme uniquement Django et le reverse proxy. Il **ne confirme pas** que le worker, le broker, le job Jenkins et l'agent `autokom-isolated` fonctionnent.
- `autokom runtime jenkins --check` vérifie la configuration du job, pas qu'un agent isolé est actif et peut réellement construire une image.
- Le script Jenkins enregistre l'archive dans `/var/lib/autokom/build-artifacts/<run_uuid>.tar`. Le broker la lit sur **l'hôte AWS**. Il faut vérifier que l'agent isolé peut la déposer à cet emplacement via un mécanisme de transfert contrôlé ; un agent GCP/distant sans stockage partagé ou transfert autorisé **ne pourra pas déployer** dans cette version.
- Le réseau Docker `--internal` limite la connectivité externe : tester la compatibilité de l'application (bases externes, APIs, e-mail, OAuth) avant de promettre l'hébergement à des tiers.
- Le broker root sur socket autorise le compte système `autokom` ; la séparation inter-tenant est actuellement assurée par les permissions Django. Une compromission du compte Unix autorisé reste un risque à traiter avant l'ouverture publique aux clients inconnus.
- Les marqueurs textuels Jenkins `AUTOKOM_STAGE` sont des **indications d'interface**, pas une attestation cryptographique qu'un scan de sécurité a eu lieu.

## Installation sur le dépôt de développement

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM_Backend_Audit_Deploy_Log_Consistency_DIRECT.zip
python3 -m unittest discover -s platform/backend/tests -p 'test_deployment_worker_consistency.py' -v
python3 -m unittest discover -s platform/backend/tests -p 'test_configuration_runner_compatibility.py' -v
python3 -m unittest discover -s platform/backend/tests -p 'test_deployment_pipeline_security.py' -v
python3 -m unittest discover -s platform/backend/tests -p 'test_socket_runtime_contract.py' -v
python3 -m compileall -q platform/backend/apps/deployments/runs platform/backend/apps/projects/configurations
```

Avec **ton environnement de développement Django**, exécuter également :

```bash
cd platform/backend
python manage.py check
python manage.py makemigrations --check --dry-run
```

Publier sur `main` après `git diff --check` et revue des changements. Sur AWS, `autokom update`, `autokom app deploy --check`, puis `autokom app deploy` lors d'une fenêtre de maintenance. Le worker est un service distinct : redémarrer **uniquement** `autokom-deployment-worker` après vérification de son unité et des autres services. Il ne faut pas refaire `autokom app setup` ni toucher aux certificats existants.

## Validation fonctionnelle réelle à effectuer

Dans l'ordre : compte client, GitHub App, création d'un projet de test, sélection du dépôt, environnement, brouillon **DOCKERFILE**, ressources accordées, branche autorisée, finalisation, `deployments/launch/`, consultation de `deployments/<id>/logs/?after=0`, résultat Jenkins, image locale, conteneur et URL HTTPS. Test d'annulation et test de rollback sur **un environnement de préproduction isolé**.

Sans test end-to-end sur ton AWS et sans vérification de l'agent Jenkins, le pipeline complet ne doit pas être annoncé comme « opérationnel ». Les tests inclus sont locaux, structuraux et unitaires.
