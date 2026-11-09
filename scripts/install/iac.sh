#!/usr/bin/env bash
# AUTOKOM IaC : Terraform + Google Cloud CLI sur l'orchestrateur Ubuntu.
# Les deux roles peuvent etre executes separement pour les captures du memoire.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/ui.sh
source "$ROOT/scripts/lib/ui.sh"
ak_init
MODE="${1:---check}"
COMPONENT="${2:-all}"
case "$MODE" in --check|--apply) ;; *) ak_err 'Mode attendu : --check ou --apply'; exit 2 ;; esac
case "$COMPONENT" in all|terraform|gcloud) ;; *) ak_err 'Outil attendu : all, terraform ou gcloud'; exit 2 ;; esac
(( EUID == 0 )) || { ak_err 'Privilèges root requis : sudo autokom iac install'; exit 1; }

ak_section 'AUTOKOM · IAC · Terraform / Google Cloud CLI'
if ! command -v ansible-playbook >/dev/null 2>&1; then
    if [[ "$MODE" == --check ]]; then
        ak_err 'Ansible absent : prévisualisation impossible (aucune installation effectuée)'
        exit 1
    fi
    # Reutilise le bootstrap officiel AUTOKOM (installation conditionnelle).
    bash "$ROOT/scripts/bootstrap/ansible.sh"
fi

export ANSIBLE_CONFIG="$ROOT/ansible/ansible.cfg"
export ANSIBLE_CALLBACK_PLUGINS="$ROOT/ansible/plugins/callback"
export ANSIBLE_STDOUT_CALLBACK='autokom'
export ANSIBLE_NOCOWS=1
if [[ "$MODE" == --check ]]; then
    export AUTOKOM_CHECK_MODE=1
else
    export AUTOKOM_CHECK_MODE=0
fi

ARGS=( -i "$ROOT/ansible/inventories/local/hosts.ini"
       "$ROOT/ansible/playbooks/iac-tools.yml"
       -e "autokom_iac_component=$COMPONENT" )
if [[ "$MODE" == --check ]]; then
    ARGS+=( --check )
fi
ansible-playbook "${ARGS[@]}"
ak_ok "IaC · ${COMPONENT} · ${MODE} terminé"
ak_finish
