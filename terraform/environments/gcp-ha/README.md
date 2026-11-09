# AUTOKOM — GCP HA : MIG régional + Autoscaler + Load Balancer

## Périmètre confirmé (9 novembre 2026)

- Projet : `plucky-stacker-480421-c4` ; VPC : `default` ; sous-réseau `default` en `us-central1`, CIDR `10.128.0.0/20`.
- Serveur orchestrateur EXISTANT : `instance-20261028-090021`, `us-central1-a`, `e2-standard-4`. **Ce module ne gère pas cette VM.**
- VM temporaire du stagiaire : `ubuntu-server-2`, `us-central1-f`, `e2-standard-2`. **Hors périmètre absolu : ne pas importer, arrêter, redimensionner, supprimer ou modifier.**
- Web 1 et web 2 : **nouvelles VM créées par le MIG**, respectivement réparties entre `us-central1-a` et `us-central1-f` en régime nominal. Les noms individuels sont générés par GCP.
- Autoscaler : minimum **2**, maximum **4** VM web, moyenne CPU cible **80 %** (`cpu_target=0.80`). En régime nominal : 1 orchestrateur + 2 workers ; en cas de forte charge : jusqu'à 4 workers. La VM temporaire reste un équipement distinct, non compté dans l'architecture AUTOKOM.

Les instances gérées peuvent être remplacées automatiquement. Toute application devra être stateless ou stocker ses données persistantes dans un service externe.

## Les ressources créées

- Un service account dédié aux nouvelles VM web (sans rôle projet implicite).
- Un modèle d'instance Ubuntu 24.04, e2-small (20 Gio), sans IP publique.
- Un MIG régional sur deux zones avec autohealing HTTP `/healthz` port 8080.
- Un Autoscaler régional CPU 0.80, 2 à 4 VM web, période de démarrage 180 s.
- Un External Application Load Balancer **global HTTP/80** avec IP IPv4 réservée et health check pour le backend 8080.
- Deux règles de firewall **ciblées exclusivement sur le tag des VM MIG** : autorisation du trafic du Load Balancer sur 8080 (priorité 1000) et refus des autres connexions entrantes (priorité 1100). Elles ne modifient pas les règles globales du VPC ni les deux VM existantes, **à condition qu’aucune VM existante ne possède déjà le tag `autokom-ha-backend`**. Vérifier ce point dans le preflight.

Le mode `bootstrap_mode="validation"` ne déploie PAS AUTOKOM ni les applications clientes. Le script de démarrage existant démarre un serveur de vérification utilisant Python déjà fourni par Ubuntu. Les VM n'ont pas d'IP publique. Les mises à jour APT et récupérations de dépôts externes nécessiteront Cloud NAT ou un mécanisme de livraison adapté, non créé par ce module.

## Identité GCP : avant toute création

Sur l'orchestrateur, les outils `terraform` et `gcloud` sont déjà installés via `sudo autokom iac install`.

L'identité actuellement attachée est le compte de service Compute Engine par défaut `589474515954-compute@developer.gserviceaccount.com`. **Ne lui accorder aucun rôle administrateur supplémentaire sans analyser les autres VM qui le partagent**, notamment la VM temporaire du stagiaire. Les permissions et les **OAuth access scopes** sont tous deux déterminants pour l'authentification depuis une VM Compute Engine.

**Recommandation :** créer sur GCP un service account utilisateur dédié `autokom-iac@plucky-stacker-480421-c4.iam.gserviceaccount.com`, avec les droits minimaux pour le provisionnement et l'accès au bucket d'état. Son rattachement à la VM principale demande normalement un arrêt/redémarrage de celle-ci ; planifier séparément cette opération, sans arrêt du serveur de production dans ce correctif. Ne pas utiliser de clé JSON téléchargée ni enregistrer des tokens dans le dépôt.

Droits à définir par un administrateur IAM selon les ressources du `terraform plan` : permissions Compute Engine pour VM, MIG, autoscaling, réseau et load balancing ; création/gestion du compte de service web ; droit `iam.serviceAccounts.actAs` pour attacher ce compte de service au modèle de VM ; accès au bucket GCS d'état. Le service account IaC ne doit pas être celui des VM web.

## État Terraform obligatoire dans GCS

**Ne pas lancer `apply` avec un état local volatile dans `/opt/autokom/source`.** Un bucket GCS privé, protégé par accès uniforme et versioning, doit exister et être accessible par l'identité Terraform.

Un administrateur peut créer ce bucket une seule fois depuis Kali (où `gcloud` est déjà connecté), **après vérification qu'il n'existe pas déjà** :

```bash
PROJECT=plucky-stacker-480421-c4
BUCKET=autokom-tfstate-589474515954
# Ne pas recréer si le bucket existe ; le nom GCS est globalement unique.
gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT" \
  --location=us-central1 --uniform-bucket-level-access --public-access-prevention
# Activer l'historique des objets
gcloud storage buckets update "gs://$BUCKET" --versioning
```

Accorder l'accès au bucket **uniquement à l'identité Terraform autorisée** (et aux administrateurs habilités). Ne pas accorder un rôle admin au compte Compute Engine par défaut pour contourner un manque de droits.

Sur le serveur principal, après synchronisation du dépôt :

```bash
cd /opt/autokom/source/terraform/environments/gcp-ha
cp -n backend.hcl.example backend.hcl
cp -n terraform.tfvars.example terraform.tfvars
chmod 600 backend.hcl terraform.tfvars
# Vérifier les deux fichiers : project, zones, 2..4 VM, CPU 0.80, bucket GCS.
./preflight.sh
./manage.sh init
./manage.sh validate
```

`backend.hcl` n'est pas versionné. Si des ressources ont déjà été créées avec un état local, **ne pas exécuter init en reconfigure ni supprimer cet état** ; planifier une migration contrôlée vers GCS, après sauvegarde du `.tfstate`.

## Étapes contrôlées de déploiement

1. Vérifier quotas, API Compute/IAM, permissions du service account orchestrateur, facturation et ressources déjà créées. Le preflight est en lecture seule.
2. `terraform.tfvars` : conserver le VPC et le sous-réseau `default`, `min_replicas=2`, `max_replicas=4`, `cpu_target=0.80`, `restrict_mig_ingress=true`, `bootstrap_mode="validation"`, `enable_https=false`.
3. Après acceptation explicite des frais prévisionnels, changer `confirm_billable_resources=true` (le champ est à `false` par défaut).
4. Depuis le serveur principal : `./manage.sh plan`, puis `./manage.sh show`. Vérifier qu'aucune VM existante n'est dans le plan.
5. Seulement après validation du plan : `./manage.sh apply` ; confirmation interactive obligatoire.
6. `./manage.sh output` ; puis vérifications avec `gcloud` :

```bash
gcloud compute instance-groups managed list-instances autokom-ha-rmig --region=us-central1 --project=plucky-stacker-480421-c4
gcloud compute autoscalers describe autokom-ha-autoscaler --region=us-central1 --project=plucky-stacker-480421-c4
gcloud compute backend-services get-health autokom-ha-backend --global --project=plucky-stacker-480421-c4
gcloud compute forwarding-rules describe autokom-ha-http --global --project=plucky-stacker-480421-c4 --format='value(IPAddress)'
```

Le Load Balancer ne sera considéré opérationnel qu'une fois le health check `HEALTHY` et la réponse HTTP du service de validation constatés. L'IP/nom de domaine existant de la plateforme AUTOKOM **n'est pas modifié**.

## Mise en production ultérieure

Une fois l'infrastructure validée, préparer la vraie application pour la VM modèle (image versionnée ou startup script reproductible), health check applicatif, secrets hors metadata et gestion des données persistantes. Ensuite seulement passer `bootstrap_mode` à `application`. Les mises à jour d'instance template peuvent créer/renouveler des VM : procéder avec une politique de rolling update et validation de santé.

HTTPS du Load Balancer est optionnel et doit être activé uniquement après création d'un nouveau sous-domaine spécifique pointant vers l'IP du LB ; ne pas écraser les domaines existants `auto.ikom.mg` ou `backend.auto.ikom.mg`.
