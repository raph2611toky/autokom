# AUTOKOM — 2D.1 : publier un artefact Jenkins vers Artifact Registry

## Point de départ effectivement validé

Le MIG `autokom-ha-rmig` est désormais composé de deux VM `worker` saines : une dans `us-central1-a` et une dans `us-central1-f`. Le sous-réseau `default` possède Private Google Access, Cloud NAT est créé, et le dépôt Docker privé `autokom-ha-images` existe.

Cette étape **ne modifie ni Terraform, ni le MIG, ni Jenkins, ni le broker, ni les domaines**. Le déploiement local actuel reste le seul mécanisme de publication client actif. La case HA demeure volontairement bloquée tant qu'il n'existe pas de réconciliation des conteneurs, de routage client et de contrôle applicatif.

## Fonctionnalité ajoutée

`scripts/ha/autokom_ha_registry.py` permet de publier **explicitement** l'image déjà produite par le runner isolé Jenkins, indexée par le `run_id` UUID. Il ne prend pas de chemin arbitraire et ne lance pas de nouveau build :

- source existante : `autokom-local:<run_uuid>` dans le démon Docker du serveur principal ;
- ou archive Jenkins, **seulement** si `--from-build-archive` est précisé : `/var/lib/autokom/build-artifacts/<run_uuid>.tar` ;
- destination : `us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images/client-<uuid_sans_tirets>:<uuid_sans_tirets>` ;
- la référence immuable par digest `@sha256:...` est affichée après vérification du registre ;
- le tag distant est refusé s'il existe déjà, pour éviter d'écraser une version ;
- authentification depuis le **compte de service réellement attaché à la VM**, via l'API des métadonnées GCE et un jeton de courte durée ;
- authentification Docker temporaire dans un dossier privé effacé après usage ; aucun fichier JSON de clé GCP ;
- pas de changement Nginx, DNS, Load Balancer, Docker clients en fonctionnement, ni Terraform.

## Installation

Depuis Kali : extraire l'archive à la racine du dépôt, `git add` des fichiers de l'archive, puis commit/push. Sur Ubuntu : `sudo autokom update` uniquement, **pas de `autokom app deploy` nécessaire** pour ces trois nouveaux fichiers autonomes.

## Vérification de l'identité (aucune écriture)

Sur le serveur principal Ubuntu :

```bash
sudo python3 /opt/autokom/source/scripts/ha/autokom_ha_registry.py --check
```

Le résultat affiche le compte de service attaché et le registre accessible. Le rôle `roles/artifactregistry.writer` a été accordé précédemment à `autokom-terraform@...`, mais **ne présumez jamais** que ce compte est celui de la VM principale. Vérifier l'email affiché et, s'il est différent, faire attribuer `roles/artifactregistry.writer` au compte réellement attaché, uniquement **sur ce dépôt**. Aucune clé GCP permanente.

Si l'API renvoie HTTP 403 : vérifier aussi les **access scopes** de la VM principale. Modifier les scopes d'une VM peut nécessiter un arrêt : ne pas le faire sur l'orchestrateur de production pendant ce chantier sans plan de maintenance.

## Publier une image déjà déployée localement

Remplacer l'UUID par celui d'un run Jenkins AUTOKOM déjà terminé :

```bash
sudo python3 /opt/autokom/source/scripts/ha/autokom_ha_registry.py \
  --run-id <run-uuid> --publish --apply
```

Si l'image n'est pas présente dans Docker, mais que l'archive Jenkins est disponible :

```bash
sudo python3 /opt/autokom/source/scripts/ha/autokom_ha_registry.py \
  --run-id <run-uuid> --from-build-archive --publish --apply
```

L'import de l'archive place une image *supplémentaire* dans le Docker du serveur principal. Il ne lance aucun conteneur et ne modifie pas le proxy. Le push peut occasionner trafic et frais de stockage dans Artifact Registry. L'accès au démon Docker exige root ; cette commande est un acte d'administration explicite, non une action d'un client.

## Confirmation depuis Kali (lecture seule)

```bash
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images \
  --project=plucky-stacker-480421-c4 --include-tags
```

Il faut une image avec son digest. Si aucun artefact Jenkins n'est encore disponible ou si `--check` échoue, ne pas publier et diagnostiquer IAM/scopes ; ne pas ajouter de clé permanente dans le dépôt.

## Évolution indispensable avant la production sur le MIG

L'étape 2D.2 ajoutera : réconciliation des images par digest depuis une source de vérité persistante, redémarrage sur remplacement de VM, secrets via mécanisme privé, contrôle des deux versions frontend/backend, exposition sécurisée par application, routage HTTPS et déploiement progressif/rollback. **La présence d'une image dans le registre ne constitue pas un déploiement sur les VM**.
