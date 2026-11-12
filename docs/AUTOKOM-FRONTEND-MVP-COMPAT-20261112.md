# AUTOKOM — correction de contrat du helper tarifaire

Le correctif porte sur `platform/frontend/src/pages/Pricing.jsx`. Le changement précédent avait fait retourner `free` lorsque l'API n'avait pas encore chargé d'abonnement, et acceptait des identifiants de plan arbitraires. La nouvelle version préserve le contrat de validation historique, tout en représentant l'offre gratuite de lancement par `free`. Elle ne réactive pas la simulation de paiement.

## Tests ciblés

```bash
cd platform/frontend
npm run test
```

Les tests complets doivent passer avant `autokom app frontend deploy --apply`. Si une autre assertion échoue, lire le journal protégé émis par la commande, par exemple `sudo sed -n '1,220p' /var/log/autokom/frontend/<journal>.log`.

## Limite

Nous n'avons pas accès au contenu du journal de test de la VM GCP. Cette correction traite une régression vérifiée du contrat `activePricingPlan`, mais ne prouve pas qu'elle est l'unique échec de la suite de tests du serveur.
