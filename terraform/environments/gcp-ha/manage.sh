#!/usr/bin/env bash
# AUTOKOM IaC : n'agit que sur les ressources definies dans terraform/environments/gcp-ha.
# Ne lance aucun "autokom install", n'importe ni ne detruit de VM preexistante.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077

if ! command -v terraform >/dev/null 2>&1; then
  echo 'Terraform absent sur le serveur orchestrateur : sudo autokom iac install' >&2
  exit 1
fi

case "${1:-}" in
  fmt)
    terraform fmt -recursive ../../
    ;;
  init)
    if [[ ! -f backend.hcl ]]; then
      echo 'backend.hcl absent. Copier backend.hcl.example, renseigner le bucket GCS existant et verifier les acces IAM.' >&2
      exit 2
    fi
    terraform init -input=false -backend-config=backend.hcl
    ;;
  validate)
    terraform validate
    ;;
  plan)
    if [[ ! -f terraform.tfvars ]]; then
      echo 'terraform.tfvars absent. Copier et adapter terraform.tfvars.example.' >&2
      exit 2
    fi
    if [[ ! -d .terraform ]]; then
      echo 'Fournisseurs/backend non initialises. Lancer ./manage.sh init.' >&2
      exit 2
    fi
    terraform plan -input=false -out=autokom-ha.tfplan
    ;;
  show)
    if [[ ! -f autokom-ha.tfplan ]]; then
      echo 'Aucun plan. Lancer ./manage.sh plan.' >&2
      exit 2
    fi
    terraform show autokom-ha.tfplan
    ;;
  apply)
    if [[ ! -f autokom-ha.tfplan ]]; then
      echo 'Aucun plan enregistre. Lancer ./manage.sh plan.' >&2
      exit 2
    fi
    echo 'ATTENTION : creation de VM GCP et de Load Balancer facturables ; les anciennes VM ne sont pas gerees.'
    read -r -p 'Saisir APPLIQUER-GCP pour confirmer : ' confirmation
    [[ "$confirmation" == 'APPLIQUER-GCP' ]] || { echo 'Annule.'; exit 3; }
    terraform apply -input=false autokom-ha.tfplan
    ;;
  output)
    terraform output
    ;;
  *)
    echo 'Usage: ./manage.sh {fmt|init|validate|plan|show|apply|output}' >&2
    exit 2
    ;;
esac
