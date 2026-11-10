# AUTOKOM — Inventaire tabulaire des applications GCP et diagnostic de livraison

Compatibilité : `scripts/lib/autokom_ha_admin.py` du commit `064e6a18c8`.

## Commandes

```bash
sudo autokom iac ha apps
sudo autokom iac ha apps -o wide
sudo autokom iac ha apps --ha-only
sudo autokom iac ha apps --worker autokom-ha-web-6j95
sudo autokom iac ha apps --diagnose
sudo autokom iac ha apps --diagnose -o json
```

La commande affiche un tableau par worker : `WORKER`, `ZONE`, `IP_PRIVEE`, `APPLICATION/DOMAINE`, `ENV`, `SANTE_APP`, `REPLICAS`, `HA`, `RELEASE`.

`--diagnose` ajoute les publications locales actives (hors MIG), à partir des métadonnées root-owned du runtime, sans afficher de variables, clés ni images Docker. Les raisons d'exclusion sont celles des garde-fous de `committed_apps()` dans `scripts/ha/autokom_ha_delivery.py` : manifeste Cloud demandé, release finalisée, image Artifact Registry, ressources admissibles, absence de variables et de stack non atomique, domaine autorisé.

## Attention à l'interprétation

* Une VM MIG `HEALTHY` ne prouve pas qu'un service client s'exécute sur la VM.
* Le manifeste GCS décrit l'affectation **désirée**, pas un `docker ps` distant.
* Le code `HEALTHY` dans `SANTE_APP` est obtenu via l'API de santé NEG/backend service GCP pour **cette release** ; ne pas le confondre avec l'état de l'instance MIG.
* Une application locale `ELIGIBLE_SANS_AFFECTATION` signale un problème de sélection ou de réconciliation à examiner ; ne jamais en déduire un déploiement réussi.
* La HA publique exige au moins deux endpoints applicatifs sains dans deux zones différentes, un routage du domaine **vers le LB HTTPS** et la réussite des contrôles HTTPS. `HA=NON_VALIDEE` est attendu tant que les étapes de bascule ne sont pas réalisées.
* Les conteneurs non déclarés/non enregistrés sur les workers ne peuvent pas être découverts par la seule API GCS/NEG : un inventaire runtime authentifié sur chaque VM serait nécessaire pour affirmer « tous les conteneurs présents ».

## Validation locale sans intervention cloud

```bash
python3 -m unittest discover -s tests -p 'test_autokom_ha_apps_20261111.py' -v
python3 -m py_compile scripts/lib/autokom_ha_admin.py scripts/lib/autokom_ha_apps.py
```

L'archive ne modifie pas le contrôleur de déploiement, les certificats, les manifests GCS, les conteneurs, le DNS, ni le Load Balancer. Pour activer la HA réelle, il faut d'abord corriger l'éligibilité ou la réconciliation révélée par `--diagnose`, puis valider les backends applicatifs et la bascule HTTPS contrôlée.
