# AUTOKOM — correctif Git des sauvegardes Terraform et précontrôle HA 70 Go

## Diagnostic du 12 novembre 2026

- `sudo autokom update` se bloquait à cause d'un fichier **non suivi** `terraform/environments/gcp-ha/terraform.tfvars.bak-20261112-173859` produit par `autokom iac ha upgrade`.
- `upgrade --check` annonçait encore 100 Go, 2–6 workers parce que le dépôt **n'avait pas été actualisé**. `terraform plan` répondait donc « No changes » pour cette ancienne configuration ; cela ne validait pas la santé du MIG ni le démarrage des nouvelles VM.
- Deux VM de remplacement étaient restées en `CREATING` / `QUOTA_EXCEEDED` sur `SSD_TOTAL_GB` (limite régionale de 500 Go).

## Modifications permanentes

1. `terraform/.gitignore` ignore exclusivement les sauvegardes générées `terraform.tfvars.bak-*` sous Terraform ; il ne masque pas les modifications des fichiers de code.
2. `scripts/lib/autokom_ha_infra_upgrade.py` écrit désormais ses sauvegardes privées dans `/var/backups/autokom/terraform-gcp-ha` (mode 0700, sauvegardes 0600), hors du dépôt Git, et ne réécrit `terraform.tfvars` qu'après le précontrôle et la confirmation.
3. `--check` construit un vrai plan Terraform pour la **cible** 70 Go `pd-balanced`, 2–5 workers, sans toucher aux paramètres locaux, et affiche le quota SSD lu chez GCP.
4. `--apply` refuse avant confirmation et sans modifier `terraform.tfvars` la création de deux VM supplémentaires si le quota libre est inférieur à **140 Go**. Le plan appliqué est revalidé après écriture des nouvelles valeurs.
5. Les protections contre les modifications Terraform de ressources non autorisées restent en vigueur.

## Installer le ZIP sur Windows PowerShell

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\AUTOKOM-HA-BACKUP-QUOTA-FIX-20261112.zip" -DestinationPath 'F:\SOUTENANCE\TOKY\autokom' -Force
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
python -m unittest discover -s tests -p 'test_autokom_ha_upgrade_backup_20261112.py' -v
git diff --check
git add terraform/.gitignore scripts/lib/autokom_ha_infra_upgrade.py tests/test_autokom_ha_upgrade_backup_20261112.py docs/AUTOKOM-HA-GIT-BACKUP-QUOTA-20261112.md
git commit -m 'fix: keep HA Terraform backups out of Git and precheck SSD quota'
git push origin main
```

## Récupérer la première sauvegarde qui bloque Git sur le serveur GCP

`autokom update` **actuellement installé** ne connait pas encore le nouveau `.gitignore`. Déplacer donc cette sauvegarde exacte (aucune suppression) *une seule fois*, avant de mettre à jour :

```bash
sudo install -d -m 0700 /var/backups/autokom/terraform-gcp-ha
sudo mv /opt/autokom/source/terraform/environments/gcp-ha/terraform.tfvars.bak-20261112-173859 /var/backups/autokom/terraform-gcp-ha/
sudo autokom update
sudo autokom iac ha upgrade --check
```

Si d'autres fichiers locaux non suivis bloquent la mise à jour, les traiter individuellement sans `git reset --hard`, `git clean -fd` ou suppression des fichiers sensibles.

Si le quota disponible est >= 140 Go et le plan autorisé est conforme :

```bash
sudo autokom iac ha upgrade --apply
# Ecrire APPLIQUER-GCP, uniquement après lecture du plan et validation des quotas.
```

Enfin contrôler **les VM réellement installées** (Terraform peut être conforme alors que la rotation reste bloquée) :

```bash
gcloud compute instance-groups managed list-instances autokom-ha-rmig --region=us-central1 --project=plucky-stacker-480421-c4
gcloud compute instance-groups managed describe autokom-ha-rmig --region=us-central1 --project=plucky-stacker-480421-c4 --format='yaml(targetSize,versions,status,updatePolicy)'
sudo autokom iac ha apps --diagnose -o wide
```

## Limites

Le correctif ne détruit pas les disques, ne contourne pas le quota Google Cloud, ne force pas `max_unavailable=2` et ne provoque pas de coupure DNS/HTTPS. Avec deux zones et `max_surge_fixed=2`, le remplacement sans indisponibilité requiert deux nouveaux disques de 70 Go **en sus des disques existants**. S'il manque du quota, la montée en capacité est bloquée jusqu'à libération contrôlée de disques orphelins ou changement d'une contrainte. La validation du HTTPS public du Load Balancer reste séparée.
