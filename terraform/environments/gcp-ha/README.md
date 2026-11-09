# AUTOKOM — Infrastructure GCP Regional MIG + Autoscaler + Load Balancer

## Portee technique

Ce module Terraform est **independant des services actuels d'AUTOKOM**. Le serveur principal reste **orchestrateur et gestionnaire principal** : Jenkins, Ansible, supervision et controle des deploiements demeurent sur ce serveur. Les VM existantes restent intactes. Terraform cree seulement un **nouveau** groupe de VM web gerees et un equilibrage de charge pour celles-ci.

- Regional MIG : au moins 2 nouvelles instances reparties sur 2 zones de `us-central1`.
- Autoscaler : 2 a 4 instances par defaut, CPU cible 60 %.
- External Application Load Balancer global : IP IPv4 statique, health check `/healthz`, backend TCP/8080, frontal HTTP/80 ; HTTPS/443 en option avec certificat gere GCP.
- Regle pare-feu **limitee par tag** aux workers du MIG depuis `35.191.0.0/16` et `130.211.0.0/22` pour TCP/8080. Pas de regle generale `0.0.0.0/0` vers les VM.
- Les VM n'ont pas d'IP externe par defaut. Pour telecharger des paquets ou des images de conteneurs, prevoir Cloud NAT ou une image de boot deja preparee. Les nouvelles VM auront un compte de service dedie sans roles GCP ajoutes implicitement.

Les MIG sont adaptes aux applications **stateless** ; stocker sessions, fichiers et donnees persistantes ailleurs avant les deploiements applicatifs.

## Mode technique de validation et futur mode application

Par defaut `bootstrap_mode="validation"` installe un service HTTP Python minimal a partir des outils deja presents sur Ubuntu 24.04 : il repond a `/healthz` et `/` avec le nom de l'instance et avertit que **ce n'est pas une application AUTOKOM**. Il permet de confirmer en conditions reelles la configuration de health check, de MIG et de LB **sans pretendre deployer le frontend/backend**.

En production, passer a `bootstrap_mode="application"` et renseigner `application_startup_script_file` (script non sensible et versionne). Le script doit rendre le vrai service disponible sur `backend_port` et `health_check_path`. Ne jamais stocker cle API, jeton ou mot de passe dans une metadata de VM ni dans Terraform state. Pour les images privees, accorder l'acces Artifact Registry seulement au service account dedie (le module ne le fait pas automatiquement). Pour une application fiable, utiliser une image immuable/versionnee, un lancement reproductible, et une politique de mise a jour du MIG. Les deux serveurs web existants ne rejoignent PAS automatiquement le MIG.

## Prerequis GCP (a controler avant tout plan)

- Projet `plucky-stacker-480421-c4` existe, facturation active, API Compute Engine et IAM disponibles.
- VPC/sous-reseau existent dans `us-central1` et les zones `us-central1-a` et `us-central1-f` sont disponibles avec quota suffisant ; adapter `terraform.tfvars` si besoin.
- Identite Terraform ayant les permissions necessaires pour Compute Engine, load balancing et creation/association de service account. Ne pas reutiliser la cle d'un compte de service privee dans le depot.
- Quotas, prix des VM 24/7, LB, IP globale et trafic verifiés. Les VMs du MIG et le load balancer generent des frais meme si les visiteurs sont peu nombreux.
- Verifier les autres regles firewall de la VPC par defaut : la nouvelle regle n'autorise que les sources du LB, mais les anciennes regles de la VPC pourraient ouvrir d'autres ports (dont SSH). Preferer VPC dediee en production.
- L'etat Terraform est local dans cette premiere phase. Pour l'exploitation multi-admin, configurer un backend GCS protege avec versioning/controle d'acces avant un premier apply collaboratif.

## Execution sur Kali ou Cloud Shell : PAS sur l'orchestrateur en production sans raison

```bash
cd /home/raph/SOUTENANCE/AUTOKOM/terraform/environments/gcp-ha
cp terraform.tfvars.example terraform.tfvars
# Editer project_id, network_name, subnetwork_name, region, zones, etc.
# Auth GCP avec ADC: gcloud auth application-default login (si travail sur Kali)
./manage.sh init
./manage.sh fmt
./manage.sh validate
```

Avant un plan, realiser ces verifications dans Cloud Shell (lecture seule) :

```bash
gcloud config set project plucky-stacker-480421-c4
gcloud compute networks list
gcloud compute networks subnets list --regions=us-central1
gcloud compute instance-groups managed list
gcloud compute forwarding-rules list --global
gcloud compute addresses list --global
```

Une fois facturation, quotas, reseau, impact et cout controles, mettre **explicitement** `confirm_billable_resources = true` dans `terraform.tfvars` (sinon le plan est bloque) :

```bash
./manage.sh plan
terraform show autokom-ha.tfplan  # inspection avant execution
./manage.sh apply                 # confirmation supplementaire au terminal
./manage.sh output
```

**Pas de `terraform apply -auto-approve`, `terraform destroy` ou import automatique de VM existantes.** Le plan peut creer **de nouvelles** VM facturables, des regles de firewall et un Load Balancer. La gestion regionale ne bascule aucun domaine existant ; il faudra changer DNS volontairement quand le service sera verifie.

## Controle GCP apres application

```bash
gcloud compute instance-groups managed list --regions=us-central1
gcloud compute instance-groups managed list-instances autokom-ha-rmig --region=us-central1
gcloud compute autoscalers list --regions=us-central1
gcloud compute backend-services get-health autokom-ha-backend --global
gcloud compute forwarding-rules describe autokom-ha-http --global --format='value(IPAddress)'
```

Verifier la health check a l'etat `HEALTHY`, consulter `terraform output validation_url_http`, puis `curl -i http://<IP_DU_LB>/healthz` (le remplacement `<IP_DU_LB>` est manuel). Pour tester la HA, simuler une panne **uniquement sur une instance de MIG de validation** et examiner l'autohealing ; le principal orchestrateur n'est jamais concerne.

## HTTPS et domaine public

L'IP globale du LB doit etre connue avant modification DNS. Creer un A record vers cette IP pour un **nouveau sous-domaine reserve** sans ecraser `auto.ikom.mg` ou `backend.auto.ikom.mg` existants. Puis configurer `enable_https=true` et `domain_name="nouveau-domaine"` et replanifier. L'emission du certificat gere Google peut necessiter du temps ; surveiller son etat. HTTP/80 reste actif dans cette version : une redirection HTTPS ne doit pas etre declaree sans etre configuree.

## Captures pour le memoire

1. Fichier `terraform/modules/gcp_regional_ha/main.tf` : instance template, role du MIG, Autoscaler et health checks (capturer deux zones significatives du code si necessaire).
2. Fichier `terraform/modules/gcp_regional_ha/main.tf` : backend service, IP globale, URL map et forwarding rule ; montrer le Load Balancer.
3. Terminal `./manage.sh plan` : plan de creation des trois services (pas une installation Ubuntu).
4. Console GCP apres un apply reel et approuve : MIG regional (instances par zone), politique d'autoscaling, backend `HEALTHY` et IP LB (2 captures d'ecran si necessaire).
