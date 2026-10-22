#!/usr/bin/env bash
set -Eeuo pipefail

# Bootstrap AUTOKOM: seul prerequis pour lancer les playbooks.
if [[ "${EUID}" -ne 0 ]]; then
  echo "[ERREUR] Utiliser sudo bash $0" >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "[ERREUR] Distribution non detectee" >&2
  exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "[ERREUR] Phase 1 cible uniquement Ubuntu (trouve: ${ID:-inconnu})" >&2
  exit 1
fi

if command -v ansible-playbook >/dev/null 2>&1; then
  echo '[OK] Ansible deja installe :'
  ansible-playbook --version | head -n 1
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ansible
ansible-playbook --version | head -n 1
echo '[OK] Bootstrap Ansible termine.'
