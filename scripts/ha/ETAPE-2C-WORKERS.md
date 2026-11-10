# AUTOKOM GCP HA — Étape 2C : préparer les workers du MIG

Cette étape **ne migre aucune application cliente**. Elle installe sur chaque nouvelle
VM du MIG un moteur Docker, vérifie l'identité GCE et la lecture d'Artifact Registry,
et conserve un endpoint technique de santé `:8080/healthz`. Les applications
existantes continuent de fonctionner sur le serveur principal.

## Préconditions confirmées

- `plucky-stacker-480421-c4`, région `us-central1`, MIG `autokom-ha-rmig`.
- NAT `autokom-ha-nat` et Private Google Access actifs.
- Dépôt `us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images`.
- Compte `autokom-ha-vm@...` avec `roles/artifactregistry.reader` au niveau dépôt.
- Ne jamais toucher à `ubuntu-server-2`, ni au serveur principal.

## Déploiement prudent

1. Déployer les fichiers de ce patch dans GitHub puis `sudo autokom update` sur
   l'orchestrateur. Ne pas exécuter `autokom app deploy` pour ce changement Terraform.
2. Sauvegarder le template précédent :
   `gcloud compute instance-groups managed describe autokom-ha-rmig --region=us-central1 --project=plucky-stacker-480421-c4 --format='value(versions[0].instanceTemplate)'`.
3. Dans **le fichier local, non versionné** `terraform.tfvars`, conserver
   `enable_cloud_nat = true` et changer **uniquement** `bootstrap_mode = "worker"`.
4. Exécuter `terraform init`, `terraform validate`, puis `terraform plan -out=autokom-ha-workers.tfplan`.
5. Le plan attendu doit créer un nouveau template et modifier la version du
   **MIG existant**, **sans remplacer le MIG lui-même**, et ne doit ni supprimer
   ni modifier le Load Balancer, le NAT ou l'autoscaler. Une modification du
   template peut entraîner un remplacement progressif des VM. Ne pas appliquer
   le plan s'il comporte d'autres destructions.
6. Après revue du plan, `terraform apply autokom-ha-workers.tfplan`.
   Surveiller `gcloud compute instance-groups managed list-instances autokom-ha-rmig
   --region=us-central1 --project=plucky-stacker-480421-c4`.
7. Confirmer deux instances `RUNNING` et deux backends `HEALTHY`.
   Vérifier les scripts de démarrage via le port série Google Cloud si nécessaire.
8. Le script de contrôle `scripts/ha/autokom_ha_preflight.py` doit afficher
   `(mode worker)` et **continuer à refuser** le déploiement client ; la
   métrique `autokom_ha_readiness_ok` reste `0`.

## Limites voulues de 2C

- Aucun agent de publication d'images client n'est encore actif.
- Aucun manifest de configuration désirée durable, ni secret de client n'est copié.
- Aucun routage par domaine, NEG ou certificat HTTPS n'est créé.
- Le check `:8080/healthz` mesure **Docker + accès GCP au bootstrap**, pas la
  santé d'une application client.
- Si le script de bootstrap échoue, le MIG peut tenter de recréer ses VM.
- Une mise à jour du template est susceptible de créer temporairement des VM
  additionnelles facturées selon la stratégie de mise à jour du MIG.
- Pour revenir en arrière, réappliquer `bootstrap_mode = "validation"` via Terraform
  et vérifier que les mêmes ressources gérées restent intactes.

L'étape suivante, 2D, doit implémenter une **réconciliation durable par application** :
push des images Jenkins par digest, contrôle des placements à chaque changement
MIG/zone, distribution idempotente des manifests, restauration des applications
sur nouvelles VM, contrôles de santé par application et routage DNS/TLS vers le
Load Balancer. Le choix HA dans Django ne doit être activé pour la publication
qu'après ces tests de bout en bout.
