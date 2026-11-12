# AUTOKOM — Tests frontend compatibles avec l'offre gratuite de lancement

## Cause des échecs

Les deux anciens fichiers `accountCheckout20261110.test.mjs` et `billingSandbox20261110.test.mjs` imposaient un parcours `/checkout/${id}` et des appels `billingApi.accountCheckout()` réservés au paiement en ligne. Ces hypothèses sont incompatibles avec la MVP où le paiement réel n'est pas disponible et où la page Tarifs dirige vers la configuration du dépôt.

## Modifications

- Actualise les assertions sans désactiver de tests.
- Vérifie que la page Tarifs affiche l'offre gratuite et accède à `/deploy`.
- Vérifie que le checkout public annonce explicitement l'indisponibilité du paiement et ne déclenche aucune activation de droit.
- Conserve les vérifications de séparation HA / souscription et des restrictions de l'API de paiement future.
- Vérifie la présence des durées 30, 15 et 7 jours dans la présentation des offres.

## Installation

Extraire l'archive à la racine du dépôt AUTOKOM. Ne pas extraire sur les VM workers : ces modifications portent uniquement sur les tests frontend.

```bash
unzip -o AUTOKOM-FRONTEND-TESTS-MVP-20261112.zip -d /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM/platform/frontend
npm ci --ignore-scripts
npm test
npm run build
```

Ne désactiver ni les tests ni les contrôles DevSecOps. Si le code n'a pas changé après `npm ci`, seules les dépendances locales seront installées. `vite: not found` indique habituellement des dépendances de développement absentes : vérifier que `NODE_ENV` et `NPM_CONFIG_PRODUCTION` ne provoquent pas l'omission des devDependencies.

Une fois `npm test` et `npm run build` réussis, effectuer commit/push sur `main` et sur GCP :

```bash
sudo autokom update
sudo autokom app frontend deploy --apply
```

Les nouveaux tests ne modifient pas les droits backend ou les traitements de paiement. Ils valident seulement le parcours actuel. Si l'application change dans le futur pour réactiver des paiements réels, il faudra introduire de nouveaux tests end-to-end spécifiques avant publication.
