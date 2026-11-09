#!/usr/bin/env bash
# Diagnostics lecture seule. Aucun changement GCP, aucun token affiche.
set -euo pipefail
PROJECT="plucky-stacker-480421-c4"
REGION="us-central1"
MIG="autokom-ha-rmig"
if ! command -v gcloud >/dev/null 2>&1; then
  echo 'gcloud absent : sudo autokom iac install' >&2
  exit 1
fi
printf '\n=== Identite gcloud active (lecture seule) ===\n'
gcloud auth list --filter='status:ACTIVE' --format='table(account,status)' || true
printf '\n=== Instances existantes : NE RIEN MODIFIER ===\n'
gcloud compute instances list --project="$PROJECT" --format='table(name,zone.basename(),status,tags.items)' || true
printf '\n=== Securite : aucune VM existante ne doit porter le tag du futur MIG ===\n'
gcloud compute instances list --project="$PROJECT" --filter='tags.items=autokom-ha-backend' --format='table(name,zone.basename(),tags.items)' || true
printf '\n=== Sous-reseau ===\n'
gcloud compute networks subnets describe default --project="$PROJECT" --region="$REGION" --format='yaml(name,ipCidrRange,network,privateIpGoogleAccess)' || true
printf '\n=== MIG du meme nom (verification de collision) ===\n'
if gcloud compute instance-groups managed describe "$MIG" --project="$PROJECT" --region="$REGION" --format='value(name)' 2>/dev/null; then
  echo 'ATTENTION : un MIG du meme nom existe deja. Verifier son etat Terraform avant tout plan.'
else
  echo 'Aucun MIG regional portant ce nom ne repond a la requete (ou permissions insuffisantes).'
fi
printf '\n=== Identite attachee a cette VM (si Compute Engine) ===\n'
curl --silent --show-error --fail --connect-timeout 2 \
  -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' || true
printf '\n\n=== Scopes OAuth de cette VM (si Compute Engine) ===\n'
curl --silent --show-error --fail --connect-timeout 2 \
  -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/scopes' || true
printf '\n'
