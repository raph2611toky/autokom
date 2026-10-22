#!/usr/bin/env bash
set -Eeuo pipefail
# Utilisation: sudo bash scripts/install/02-install-foundation.sh [--check|--apply]
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:---check}"
if [[ "$MODE" != '--check' && "$MODE" != '--apply' ]]; then
  echo 'Usage: sudo bash scripts/install/02-install-foundation.sh [--check|--apply]' >&2
  exit 2
fi
if [[ "${EUID}" -ne 0 ]]; then
  echo '[ERREUR] Lancer via sudo.' >&2
  exit 1
fi
if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo '[ERREUR] Installer Ansible d abord: sudo bash scripts/bootstrap/01-install-ansible.sh' >&2
  exit 1
fi
ARGS=()
if [[ "$MODE" == '--check' ]]; then
  ARGS+=(--check --diff)
fi
export ANSIBLE_CONFIG="$ROOT/ansible/ansible.cfg"
ansible-playbook -i "$ROOT/ansible/inventories/local/hosts.ini" \
  "$ROOT/ansible/playbooks/01-foundation.yml" "${ARGS[@]}"
