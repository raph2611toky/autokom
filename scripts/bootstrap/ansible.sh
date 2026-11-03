#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/ui.sh
source "$ROOT/scripts/lib/ui.sh"
ak_init
(( EUID == 0 )) || { ak_err 'Privilèges root requis'; exit 1; }
if command -v ansible-playbook >/dev/null 2>&1; then
    ak_ok "Ansible déjà présent : $(ansible-playbook --version | head -n 1)"
    exit 0
fi
export DEBIAN_FRONTEND=noninteractive
ak_run 'Actualiser les index APT' apt-get update -qq
ak_run 'Installer Ansible' apt-get install -y -qq --no-install-recommends ansible
ak_ok "Moteur disponible : $(ansible-playbook --version | head -n 1)"
