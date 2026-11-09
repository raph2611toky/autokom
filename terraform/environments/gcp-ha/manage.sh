#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077
if ! command -v terraform >/dev/null 2>&1; then
  echo 'Terraform absent : installer Terraform CLI sur Kali ou Cloud Shell.' >&2
  exit 1
fi
case "${1:-}" in
  fmt)
    terraform fmt -recursive ../../
    ;;
  init)
    terraform init -input=false
    ;;
  validate)
    terraform validate
    ;;
  plan)
    if [[ ! -f terraform.tfvars ]]; then
      echo 'Copier terraform.tfvars.example vers terraform.tfvars puis verifier les valeurs.' >&2
      exit 2
    fi
    terraform plan -input=false -out=autokom-ha.tfplan
    ;;
  apply)
    if [[ ! -f autokom-ha.tfplan ]]; then
      echo 'Aucun plan enregistre ; lancer ./manage.sh plan avant apply.' >&2
      exit 2
    fi
    echo 'ATTENTION : ce plan cree des ressources GCP FACTUREES.'
    read -r -p 'Saisir APPLIQUER-GCP pour confirmer : ' confirmation
    [[ "$confirmation" == 'APPLIQUER-GCP' ]] || { echo 'Annule.'; exit 3; }
    terraform apply -input=false autokom-ha.tfplan
    ;;
  output)
    terraform output
    ;;
  *)
    echo 'Usage: ./manage.sh {fmt|init|validate|plan|apply|output}' >&2
    exit 2
    ;;
esac
