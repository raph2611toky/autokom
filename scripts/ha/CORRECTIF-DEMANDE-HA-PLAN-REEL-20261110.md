# AUTOKOM — Déploiement bloqué par une demande HA implicite

## Problème confirmé

Le frontend activait automatiquement la HA quand le client sélectionnait l'offre « Professionnel ».
Or une sélection de prix ne crée jamais un `ApplicationEntitlement` premium.
À la création, AUTOKOM accorde le plan gratuit (une seule instance). La finalisation
refuse donc la configuration avant la création du run Jenkins.

## Correction

- Tous les plans, y compris Professionnel, ont la HA **désactivée par défaut**.
- Le client peut explicitement cocher l'option HA, mais elle ne s'active pas sans droit réel.
- L'écran distingue offre souhaitée et droits accordés.
- Le backend retourne désormais `max_instances` et l'état de l'option HA dans l'API des offres.
- Un refus indique comment redevenir éligible sans supprimer le brouillon.
- L'admin Django permet maintenant d'ajouter une `HighAvailabilityOption` en choisissant l'environnement.
- `autokom_ha_rights` diagnostique la situation réelle sans attribuer de droits.

## Activer sur Ubuntu

Après `sudo autokom update` :

```sh
sudo autokom app deploy --check && sudo autokom app deploy
sudo autokom app frontend deploy --check && sudo autokom app frontend deploy --apply
```

Le diagnostic des droits réels est directement intégré dans la CLI, sans devoir
charger manuellement les secrets du backend :

```sh
sudo autokom iac ha rights --list
sudo autokom iac ha rights --project UUID_PROJET
sudo autokom iac ha rights --environment UUID_ENVIRONNEMENT
```

Ces commandes sont **en lecture seule**, utilisent le compte de service Django et ne
créent aucun droit commercial. Elles exigent `sudo` pour protéger les données projets.

## Deux choix distincts

**Déploiement standard** : revenir dans Configuration et **décocher** la HA, puis relancer.
C'est autorisé par un droit à une instance, selon les limites habituelles de l'offre.
Le contrôleur cloud cible un seul worker pour ce mode.

**Déploiement HA** : un administrateur doit d'abord attribuer **un vrai plan actif**
autorisant au moins deux instances et une `HighAvailabilityOption` de statut `ACTIVE`.
Le modèle exige un administrateur, une référence de facturation réellement vérifiée
et la date d'activation. Ces informations ne sont **jamais** auto-remplies par le
formulaire client. La plateforme ne possède pas encore un paiement automatisé.

Dans l'administration Django, vérifier le catalogue `ResourcePlan` (plan actif,
`max_instances >= 2`, CPU/RAM cohérents), puis `ApplicationEntitlement`
(`status=ACTIVE`, `grant_source=ADMIN`, administrateur identifié, bon `resource_plan`)
et `HighAvailabilityOption` (environnement choisi, statut `ACTIVE`, `desired_replicas=2`,
référence de facturation vérifiée). Aucun accès IAM supplémentaire n'est nécessaire.

## Validation

`node --test tests/highAvailabilityOptIn20261110.test.mjs` et tests frontend existants.
La migration Django n'est pas nécessaire. Ne pas modifier les MIG, DNS ou le
Load Balancer pour corriger le droit du projet.
