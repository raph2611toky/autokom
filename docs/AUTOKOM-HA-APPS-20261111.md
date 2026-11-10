# AUTOKOM · Inventaire des applications HA par worker

Extension additive compatible avec la structure du commit `ef482b99de`.

## Commandes

```bash
sudo autokom iac ha apps
sudo autokom iac ha apps --ha-only
sudo autokom iac ha apps --worker autokom-ha-web-6j95
sudo autokom iac ha apps --json
autokom iac ha -h
```

La lecture seule ne configure aucun conteneur, DNS, certificat, MIG, NEG ou Load Balancer.
`sudo` est conseillé pour accéder au rapport de livraison de permissions `0600`.

## Signification des résultats

- `VM HEALTHY` : contrôle d'infrastructure de l'instance MIG, pas celui de l'application.
- Manifeste GCS : affectation souhaitée à un worker, non preuve de déploiement.
- `app=HEALTHY` : santé de l'endpoint applicatif vérifiée auprès du backendService GCP.
- `REPLIQUES_PRETES_HTTPS_NON_VALIDE` : deux répliques applicatives saines dans deux zones distinctes, mais bascule HTTPS/DNS client non attestée.
- `ACTIVE` : réservé à un rapport disposant explicitement d'une validation HTTPS et routage public, plus deux répliques saines de même release.
- `INCONNUE`, `NON_VALIDEE`, `ABSENT` : états sûrs, sans faux positif.
- `Aucune application déclarée` : aucun service dans le manifeste du worker, même si la VM MIG est `HEALTHY`.
- `Inventaire ... INDISPONIBLE` : GCS illisible/inaccessible ; ne jamais assimiler à une liste vide.

La version actuelle du contrôleur HA ne produit pas encore une validation HTTPS/DNS publique. La nouvelle sous-commande ne l'active pas par elle-même.

## Contrôle qualité

```bash
python3 -m unittest discover -s tests -p 'test_autokom_ha_apps_20261111.py' -v
python3 -m py_compile scripts/lib/autokom_ha_admin.py scripts/lib/autokom_ha_apps.py
```

Dans AUTOKOM, le déploiement HA exige une image publiée, une configuration applicative éligible, un manifeste GCS, une réconciliation réussie puis la vérification des endpoints. Publier l'image seule ne suffit pas à rendre l'application présente sur les workers.
