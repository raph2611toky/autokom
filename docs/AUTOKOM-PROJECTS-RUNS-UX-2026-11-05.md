# AUTOKOM — Publications depuis Mes projets

## Source et portée

Correctif préparé sur la base AUTOKOM `a484fa65be` (agent Jenkins `autokom-builder-01` déjà provisionné). **Aucune migration Django, aucun changement du socket du broker, de Jenkins, des domaines ou de Docker.** Les vues existantes de démonstration demeurent intactes. Le frontend exploite les routes Django existantes.

## Interface

- « Mes projets → projet » : bouton **Déployer maintenant**. Le clic valide le plan côté Django (`GET deployments/preview/`), puis crée une exécution avec un seul `POST deployments/launch/`. Pas de boîte de confirmation du navigateur.
- Le bouton devient indisponible si le projet est archivé, si aucune configuration finalisée n'existe ou si une exécution est déjà en cours. Le serveur effectue aussi ces vérifications : l'interface n'est pas un contrôle de sécurité.
- Une même clé d'idempotence est conservée en cas de réponse réseau ambiguë; les clics concurrents sont bloqués synchroniquement.
- Les informations de configuration sont repliées par défaut. Les 10 publications les plus récentes apparaissent en premier, avec leur état, leur date et un lien vers les détails.
- `/projects/<id>/history` : historique paginé (20 exécutions par page), calculé à partir de `count` retourné par Django. Toutes les publications restent accessibles, sans télécharger tout l'historique en mémoire.
- `/projects/<id>/deployments/<run_id>` : page dédiée avec étapes verticales, journal client filtré, configuration de la publication, actualisation des publications actives et récupération des messages par curseur `after` (100 entrées par lot maximum).
- Les statuts (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`) proviennent exclusivement de Django. Un ancien `CANCELLED` reste visible comme « Annulé ».

## Contrat backend existant vérifié dans le code

| Fonction | Route | Contrôle existant |
|---|---|---|
| Validation | `GET /api/projects/<p>/environments/<e>/deployments/preview/` | `plan_deployment()` |
| Mise en file | `POST .../deployments/launch/` | session, accès en écriture, `Idempotency-Key`, refus d'exécution concurrente, broker prêt |
| Historique | `GET .../deployments/?page=N&page_size=20` | accès à l'environnement, filtrage sur l'environnement, tri par date décroissante, limite 50/page |
| Détails | `GET .../deployments/<run>/` | `accessible_run()` vérifie l'appartenance au projet/environnement |
| Journal | `GET .../deployments/<run>/logs/?after=NN` | lecture des 100 entrées suivantes, séquence croissante, contrôles d'accès |

`GET .../deployments/` fournit `count`, `page` et `runs`. Le frontend utilise `count` pour proposer la navigation dans l'historique complet.

## Préparation et déploiement

### Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Projects_ManualRuns_UX_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Projects_ManualRuns_UX_2026-11-05.patch
npm --prefix platform/frontend test
python3 -m unittest -v tests.test_autokom_project_runs_contract
git diff --check
git add platform/frontend/src platform/frontend/tests tests/test_autokom_project_runs_contract.py docs/AUTOKOM-PROJECTS-RUNS-UX-2026-11-05.md
git commit -m "feat: project manual deployments and readable run history"
git push origin main
```

### Ubuntu AWS

```bash
sudo autokom update
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

Le backend n'a pas changé et ne nécessite donc pas de redéploiement pour ce patch. Ne relancez pas le setup Jenkins, l'agent, Docker, Nginx ou Let's Encrypt. Le frontend reste publié atomiquement par AUTOKOM avec ses contrôles DevSecOps existants.

## Recette réelle après publication

1. Vérifier le statut du projet `portfolio`, sa configuration finalisée et le nouveau bouton.
2. Si une exécution est encore `QUEUED` ou `RUNNING`, la suivre plutôt que lancer un doublon. En l'absence d'exécution active, cliquer sur « Déployer maintenant » : **cela effectue un vrai lancement**, pas un test.
3. Vérifier que le navigateur accède à `/projects/<id>/deployments/<id>` et que les données viennent de Django.
4. Vérifier la progression, les journaux, puis l'état final. Utiliser « Voir tout l'historique » pour valider les pages.
5. Si un `403`, `409` ou `503` apparaît, ne pas supprimer les déploiements ni contourner les contrôles : vérifier l'autorisation, l'exécution concurrente ou `sudo autokom runtime doctor`.

Les tests JavaScript sont locaux. Les tests de bout en bout Django/MySQL, la construction BuildKit, l'exécution Jenkins et la publication HTTPS doivent être validés sur AWS ; les 3 premiers ne sont pas simulés comme réussis par ce patch.
