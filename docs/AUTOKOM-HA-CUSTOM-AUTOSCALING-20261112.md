# AUTOKOM — Migration HA 2 vCPU / 4 Go / 100 Go et autoscaling lié aux applications

## Périmètre exact du correctif

- MIG régional `autokom-ha-rmig`, zones `us-central1-a` et `us-central1-f`.
- Template VM `e2-custom-2-4096`, disque système **100 Go `pd-balanced`**, instances privées et mêmes tags/IAM/NAT que le déploiement existant.
- Autoscaler **2 à 6 workers**, CPU cible **55 %**, initialisation de politique à **90 s** ; **30 min de stabilisation du scale-in** pour éviter les oscillations. Le bootstrap Ubuntu/Docker peut toujours prendre plusieurs minutes ; 90 s n'est pas une promesse de VM utilisable en 90 s.
- Réconciliation applicative : **3 072 Mio et 1 600 millicœurs** réservés aux conteneurs sur chaque nouveau worker, le reste pour Ubuntu/Docker/Nginx. Une application isolée ne peut pas demander plus de 2 800 Mio / 1 600 millicœurs.
- Le contrôleur relève automatiquement `minNumReplicas` à hauteur des demandes lorsque la capacité est insuffisante, en conservant le scaling CPU et les plafonds GCP. Le nouveau planificateur préserve **2 zones distinctes par application HA**. Le minimum redescend après 30 min de demande moindre, sans effacer les autres paramètres de l'autoscaler.
- Pendant le remplacement progressif, un ancien worker `e2-small` reste limité à son **ancien budget de 1 500 Mio** : aucune surcharge temporaire ne lui est attribuée.
- Un projet supprimé d'AUTOKOM est exclu de la configuration désirée GCS. Ses conteneurs et routes Nginx disparaissent lors du cycle agent ; le contrôleur retire également sa règle spécifique du **URL map HTTP** lorsque la suppression locale est confirmée. Les autres URL maps et le Load Balancer ne sont pas détruits.
- L'inventaire et le statut restent **honnêtes** : `SCALING_UP`, `WAITING_WORKER_UPGRADE`, `WAITING_FOR_HEALTH`, `HTTP_LB_READY_NOT_HTTPS`, etc. Le HTTPS public n'est **pas** activé par ce correctif.

## Installer dans le dépôt Git local

L'archive ZIP contient uniquement les fichiers complets aux chemins relatifs du dépôt (aucun dossier englobant). Depuis Kali :

```bash
unzip -o /home/raph/Downloads/AUTOKOM-HA-CUSTOM-AUTOSCALING-20261112.zip -d /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM
python3 -m pytest -q tests/test_autokom_ha_*.py
git diff --check
git status --short
# Lire le diff, puis commit et push sur origin/main
```

## Actualiser le serveur principal puis nettoyer l'application supprimée

```bash
sudo autokom update
sudo autokom iac ha delivery --check
sudo systemctl start autokom-ha-delivery.service
sudo autokom iac ha delivery --status
sudo autokom iac ha apps -o wide
```

Le projet supprimé (`ladyportfolio`, environnement `94ff7746-5db4-40ee-b3cd-6f2e6ea7e1d0`) **ne doit plus apparaître dans les manifestes courants** après deux cycles. Vérifier qu'il a effectivement été supprimé dans AUTOKOM et que le broker `runtime.deprovision` a effacé son `current.json` et ses marqueurs d'appartenance. Sinon, ne pas supprimer les ressources à la main : traiter d'abord l'échec de déprovisionnement.

```bash
sudo test ! -f /var/lib/autokom/client-runtime/94ff7746-5db4-40ee-b3cd-6f2e6ea7e1d0/current.json && echo 'Publication locale supprimée'
sudo autokom iac ha apps --diagnose -o wide
```

## Mise à niveau du MIG : une seule commande AUTOKOM

La commande lit les **vraies valeurs** de `terraform/environments/gcp-ha/terraform.tfvars` sur le serveur principal. Elle conserve tous les paramètres réseau, NAT, bucket, bootstrap et permissions. Elle ne modifie pas le dépôt Git ou les secrets.

```bash
sudo autokom iac ha upgrade --check
```

Le précontrôle génère un plan Terraform en lecture seule, vérifie ses actions et **refuse** toute destruction du MIG, toute modification des LB/IP, du NAT, du bucket, des règles réseau ou des comptes IAM. Le plan autorise seulement la nouvelle instance template, la mise à jour *en place* du MIG et la politique de l'autoscaler.

Si le plan est conforme :

```bash
sudo autokom iac ha upgrade --apply
```

Après la commande, saisir `APPLIQUER-GCP`. Elle sauvegarde `terraform.tfvars`, prépare et applique le plan approuvé. Les mises à jour sont `PROACTIVE`, `SUBSTITUTE`, `max_surge_fixed=2`, `max_unavailable_fixed=0`, et attendent 300 secondes après la disponibilité d’infrastructure des nouvelles VM, pour laisser les agents et manifestes converger. Cette attente ne vérifie toutefois pas la santé applicative avant retrait des anciennes VM. Le fournisseur GCP et le quota peuvent imposer des délais et erreurs de stock ; aucun « zero downtime » n'est garanti pendant les changements d'instance tant que la santé applicative et le routage public HTTPS ne sont pas validés.

**Ne pas** lancer en parallèle `gcloud ... resize`, `delete-instances`, `stop`, `recreate-instances` ou `terraform apply` depuis une autre session. Le MIG doit rester sous autoscaler et Terraform.

## Surveillance et validation (répéter jusqu'à stabilisation)

```bash
sudo autokom iac ha list
sudo autokom iac ha apps --diagnose -o wide
sudo autokom iac ha delivery --status
sudo systemctl status autokom-ha-delivery.timer --no-pager
sudo journalctl -u autokom-ha-delivery.service -n 60 --no-pager

gcloud compute instance-groups managed describe autokom-ha-rmig \
  --region=us-central1 --project=plucky-stacker-480421-c4 \
  --format='yaml(targetSize,versions,updatePolicy,status)'

gcloud compute instance-groups managed list-instances autokom-ha-rmig \
  --region=us-central1 --project=plucky-stacker-480421-c4

gcloud compute instance-groups managed describe autokom-ha-rmig \
  --region=us-central1 --project=plucky-stacker-480421-c4 \
  --format='value(versions[0].instanceTemplate)'

gcloud compute instance-groups managed describe autokom-ha-rmig \
  --region=us-central1 --project=plucky-stacker-480421-c4 \
  --format='value(status.isStable)'
```

Vérifier pour les nouvelles instances : `machineType=e2-custom-2-4096`, disque de boot de 100 Go, santé MIG `HEALTHY`; les applications HA doivent disposer de deux endpoints `HEALTHY` situés dans deux zones distinctes. Pour la montée en capacité, lancer `sudo autokom iac ha delivery --check` et lire `capacity.required`/`capacity.target`/`capacity.status` ; **aucune surcharge artificielle de production** n'est nécessaire. Une simulation `--check` n'ajoute aucune VM.

Une erreur IAM `compute.autoscalers.update`, un quota Compute Engine insuffisant ou une pénurie régionale se traduit par `CAPACITY_SCALE_ERROR` et doit être corrigée côté autorisations/quota. N'augmenter le plafond de six VM qu'après vérification des quotas et de la distribution des zones.

## HTTPS public et bascule DNS : étape distincte

Les workers et le routage HTTP GCP ne suffisent pas à déclarer la HA publique. Le domaine `*.auto.ikom.mg` doit disposer d'un certificat valide **sur le Load Balancer**, puis d'un basculement DNS/Cloudflare coordonné.

Le template actuel ne crée qu'un certificat managé Compute Engine pour **un** `domain_name` lorsque `enable_https=true`; il ne fournit pas de certificat wildcard pour tous les clients. Pour un SaaS avec nombreux sous-domaines, utiliser **Certificate Manager avec autorisation DNS wildcard** ou un certificat approprié déjà détenu, puis configurer le HTTPS proxy et une bascule DNS vérifiée. Les certificats Google-managed Compute Engine classiques ne prennent pas en charge les wildcards. **Ne pas** modifier maintenant l'A record ou désactiver le proxy HTTPS du serveur principal.

Le statut `HTTP_LB_READY_NOT_HTTPS` reste donc explicitement non public. Ce correctif **n'active pas** de manière trompeuse le mode HA dans les interfaces tant que DNS, TLS et acheminement public ne sont pas vérifiés.

## Retour arrière

- Si le plan Terraform comporte des changements inconnus, ne pas appliquer : aucun changement GCP n'est effectué.
- `terraform.tfvars.bak-<date>` est conservé sur le serveur ; en cas d'échec **avant** l'application Terraform, restaurer son contenu et refaire un plan.
- En cas de remplacement de VMs déjà commencé, **ne pas supprimer** les nouvelles instances manuellement ; examiner le statut MIG, les quotas et les logs avant tout retour vers l'ancien template.
- Le `google_compute_instance_template` crée de nouvelles instances avec un disque de 100 Go ; le disque de chaque ancien worker est jetable par conception. Les images applicatives restent dans Artifact Registry et les manifests désirés privés GCS.
