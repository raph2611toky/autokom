# AUTOKOM HA — correction du bootstrap Docker lent (10 novembre 2026)

## Diagnostic provenant des journaux des VMs du MIG

Sur autokom-ha-web-pqzh, le telechargement apt pour docker.io/containerd/runc a dure
10 min 16 s. La periode initiale d'autohealing etait de 360 secondes (6 min) et
le worker est entre en etat UNHEALTHY puis RECREATING.

Cette correction conserve la veritable probe /healthz (qui retourne 503 si la
preparation n'est pas achevee) et ne modifie ni le code Docker, ni le routage,
ni le template. Elle etend uniquement le delai de grace pour le mode worker et
les modes applicatifs de 360 a 1800 secondes. La validation conserve 360 s.

## 1) Mesure immediate (depuis Kali), sans toucher aux VMs

    gcloud compute instance-groups managed update autokom-ha-rmig \
      --project=plucky-stacker-480421-c4 --region=us-central1 \
      --initial-delay=1800

    gcloud compute instance-groups managed describe autokom-ha-rmig \
      --project=plucky-stacker-480421-c4 --region=us-central1 \
      --format='yaml(autoHealingPolicies,currentActions,status.isStable,status.versionTarget.isReached,updatePolicy.type)'

Ceci met a jour la politique du MIG en place; ne relance pas de rolling-action et
ne supprime pas de VMs. Si certaines VMs etaient deja en RECREATING, cela ne
restaure pas leur demarrage precedent; les suivantes beneficieront du nouveau delai.

## 2) Conserver le reglage dans Terraform

Extraire le ZIP a la racine du depot AUTOKOM, committer/pusher les 6 fichiers
fonctionnels et le test, puis sur Ubuntu `sudo autokom update`.

La variable `worker_initial_delay_seconds` vaut 1800 par defaut; la renseigner
explicitement dans le terraform.tfvars local est recommande. Avant d'appliquer
Terraform, executer `terraform validate` et `terraform plan` depuis
`/opt/autokom/source/terraform/environments/gcp-ha` avec backend.hcl existant.

L'application Terraform n'est necessaire que si le plan indique le changement
attendu en place du delai de grace, **sans remplacement de VM, MIG, NAT, LB**.
Si la commande gcloud a deja defini 1800, le plan devrait ne pas proposer ce
changement. Ne pas appliquer un plan destructeur.

## 3) Apres demarrage, verification technique

Rechercher dans la sortie serie des NOUVEAUX workers :

    AUTOKOM_HA_WORKER_PROBE_OK
    AUTOKOM_HA_WORKER_PREPARED

En cas de `AUTOKOM_HA_WORKER_PROBE_ERROR HTTP_403`, verifier scopes/IAM du compte
`autokom-ha-vm`. Ce correctif ne change pas la politique de permissions ni ne
cache un echec durable de la probe.

## Evolution de production

Pour accelerer le scale-out et la reprise sur panne, construire a terme une
image VM durcie avec Docker deja installe (Packer / image personnalisee GCE).
Le delai de grace de 1800 s n'est qu'une tolerance aux premiers bootstrap lents.
