# AUTOKOM HA — adaptation au quota SSD de l’essai Google Cloud

Configuration : worker `e2-custom-2-4096` (2 vCPU / 4 GiB), disque système de 70 GiB `pd-balanced`, MIG régional 2 à 5 workers et cible CPU à 55 %. Deux zones conservées. Le contrôleur de livraison respecte également la limite de 5 workers.

**Ne pas confondre dimensionnement et disponibilité du quota :** pendant le remplacement progressif actuel (`max_surge_fixed=2`, `max_unavailable_fixed=0`), deux VM de 70 GiB peuvent être ajoutées avant retrait des anciennes. Il faut donc **140 GiB de quota SSD encore disponible** dans `us-central1` pour garantir la création simultanée des deux remplaçantes, et des quotas CPU suffisants. `pd-balanced` consomme le quota `SSD_TOTAL_GB`. Le correctif ne libère aucun autre disque et ne contourne pas les quotas imposés par Google.

Le MIG existant peut rester momentanément en mode rolling update après `terraform apply`; le correctif ne provoque aucune suppression manuelle de VMs. Avant d'appliquer le changement, lancer le contrôle du quota dans PowerShell :

```powershell
$PROJECT = 'plucky-stacker-480421-c4'
$quotas = (gcloud compute regions describe us-central1 --project=$PROJECT --format='json(quotas)' | ConvertFrom-Json).quotas
$quotas | Where-Object { $_.metric -match 'SSD|DISK|CPU' } | Format-Table metric,usage,limit -AutoSize
```

Il faut confirmer la marge `limit - usage >= 140` pour le quota SSD **avant de demander un remplacement sans indisponibilité**. Si moins de 140 GiB sont disponibles, il faudra libérer des disques SSD *orphelins vérifiés* ou choisir, après validation explicite, une stratégie de migration différente ; ne pas supprimer les disques actifs ni forcer le MIG en mode `max_unavailable=2`.

## Installation du patch sur Windows (PowerShell)

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\AUTOKOM-HA-TRIAL-70GB-20261112.zip" -DestinationPath 'F:\SOUTENANCE\TOKY\autokom' -Force
```

Mettre à jour la branche Git du dépôt Windows, puis sur la VM GCP :

```bash
sudo autokom update
sudo autokom iac ha upgrade --check
sudo autokom iac ha upgrade --apply
# Répondre uniquement APPLIQUER-GCP si le plan ne contient que le template, le MIG et l’autoscaler.
```

Contrôler ensuite le MIG (`gcloud compute instance-groups managed list-instances autokom-ha-rmig --project=plucky-stacker-480421-c4 --region=us-central1`) et `sudo autokom iac ha apps --diagnose -o wide`.

La haute disponibilité publique reste conditionnée à la santé des deux répliques de chaque application, à HTTPS et à la bascule DNS vérifiés.
