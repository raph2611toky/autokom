# AUTOKOM HA — Correction de compatibilité Terraform Google (12 novembre 2026)

## Constat

`autokom iac ha upgrade --check` échouait à l'étape `terraform validate` sur
`google_compute_region_instance_group_manager.web`: l'argument
`update_policy.min_ready_sec = 300` n'est pas accepté par le fournisseur
`hashicorp/google` actuellement verrouillé par l'environnement GCP HA.
Ce paramètre est réservé à l'API/provider bêta.

## Modification

Seul l'argument bêta `min_ready_sec` a été retiré du module Terraform.
La stratégie `PROACTIVE`, `SUBSTITUTE`, `max_surge_fixed = 2` et
`max_unavailable_fixed = 0` est conservée. La politique d'autohealing
et la sonde HTTP du MIG restent en place.

Le retrait de `min_ready_sec` signifie qu'aucune attente **additionnelle fixe**
de 300 secondes n'est imposée après une réponse saine ; la sonde de santé
GCP vérifie néanmoins les nouvelles instances. Cela ne valide pas à elle
seule le routage applicatif HTTPS des clients.

## Déploiement

Sur Kali :

```bash
unzip -o /home/raph/Downloads/AUTOKOM-HA-TERRAFORM-FIX-20261112.zip -d /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM
python3 -m unittest discover -s tests -p 'test_autokom_ha_terraform_provider_20261112.py' -v
git diff --check
git status --short
```

Vérifier le diff, faire le commit et pousser sur origin/main.
Sur le serveur principal GCP :

```bash
sudo autokom update
sudo autokom iac ha upgrade --check
```

**Ne lancer `sudo autokom iac ha upgrade --apply` que si `--check` montre
uniquement le template, le MIG et l'autoscaler autorisés, sans changements
inattendus**. Le plan Terraform doit rester sous contrôle pendant la rotation.

Surveillance après apply :

```bash
gcloud compute instance-groups managed describe autokom-ha-rmig --region=us-central1 --project=plucky-stacker-480421-c4 --format='yaml(targetSize,versions,updatePolicy,status)'
gcloud compute instance-groups managed list-instances autokom-ha-rmig --region=us-central1 --project=plucky-stacker-480421-c4
sudo autokom iac ha apps --diagnose -o wide
sudo autokom iac ha delivery --status
```

En cas de nouvelle erreur `validate`, ne pas appliquer le plan avant de
corriger l'argument concerné. Aucune VM n'a été modifiée par le `--check`
qui a échoué.
