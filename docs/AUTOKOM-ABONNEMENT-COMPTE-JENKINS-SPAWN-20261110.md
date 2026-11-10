# AUTOKOM — Abonnement du compte et correction Jenkins `spawn helper`

## Portée

- `Tarifs` : Découverte continue vers Déployer ; Essentiel/Professionnel ouvrent `/checkout/<offre>`.
- `Paiement` : confirmation de démonstration explicite **sans débit ni données bancaires**. La source de vérité reste le backend, jamais `localStorage`.
- `Mon compte` : abonnement actif, période, expiration, instances autorisées, disponibilité de l'option HA et confirmation de simulation.
- `Déployer` et `StackDeploy` : formule **en lecture seule** selon le compte connecté ; aucune souscription initiée depuis l'application. La HA reste désactivée par défaut, avec opt-in explicite si le compte possède les droits.
- MySQL : nouvelle table `plans_accountsubscription` et environnement nullable pour la preuve de simulation, afin qu'un paiement puisse avoir lieu *avant* la création d'un projet. Les anciennes preuves liées aux environnements sont conservées.
- Souscription : accorde le plan aux environnements dont le compte est propriétaire, sans remplacer les droits `ADMIN` ou `PAYMENT`. Tout nouvel environnement du même propriétaire hérite du droit, et l'option HA est accordée mais **pas activée dans la configuration de déploiement**.
- Le mode administrateur `sudo autokom app --mode-prod` désactive toute nouvelle simulation et rend les droits simulés inéligibles à de nouvelles publications. Les publications existantes ne sont pas détruites automatiquement.
- Aucune intégration à un prestataire de paiement réel : avant exploitation commerciale, remplacer le fournisseur de simulation par un prestataire vérifiant une transaction effective. Ne jamais considérer un reçu simulé comme un paiement.

## Installation

Sur Kali, extraire l'archive **directement à la racine du dépôt**. Ajouter exclusivement les fichiers du ZIP, vérifier les changements puis pousser sur `main`.

Sur Ubuntu principal :

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app --mode-status
```

Il faut s'assurer que `--mode-test` reste activé pendant l'essai avec le compte explicitement autorisé. Les migrations Django sont appliquées par `autokom app deploy`. Le test Django `apps.plans.entitlements.test_account_checkout_20261110` doit être lancé avec une **base de test dédiée**, jamais la base de production.

## Incident Jenkins #32

Cause dans le journal : `java.io.IOException: Cannot run program "nohup": Failed to exec spawn helper` sur le nœud `autokom-builder-01`, avant toute récupération GitHub. Cela peut survenir lorsque la JVM du service agent reste en mémoire après une mise à jour du JDK sur disque.

```bash
sudo python3 /opt/autokom/source/scripts/lib/autokom_jenkins_spawn_doctor.py
```

Si l'agent n'exécute **aucun build**, redémarrer **uniquement** l'agent (la commande n'agit ni sur Jenkins controller, ni sur les workers GCP) :

```bash
sudo python3 /opt/autokom/source/scripts/lib/autokom_jenkins_spawn_doctor.py --repair --confirm-no-builds
sudo journalctl -u autokom-jenkins-agent.service -n 60 --no-pager
```

Relancer ensuite une **nouvelle publication** AUTOKOM. L'ancien build #32 restera en échec dans l'historique. Si le problème persiste, contrôler les versions du JDK, l'exécutabilité de `jspawnhelper` et les journaux du service avant toute nouvelle modification.

## Validation

- 196 tests Node frontend exécutés et réussis dans la copie du dépôt.
- 4 tests Python du doctor Jenkins réussis.
- Sources Python compilées syntaxiquement et composants JSX parsés.
- Les migrations Django et la souscription réelle via l'API ont des tests intégrés, **non exécutés ici** (Django/MySQL indisponibles dans cette session). Vérifier les commandes `--check` et les migrations sur Ubuntu avant publication.
- Aucun redémarrage Jenkins, aucune mutation de GCP, aucun paiement et aucune migration MySQL réalisés hors de votre serveur.
