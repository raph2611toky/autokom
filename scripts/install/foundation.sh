#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/ui.sh
source "$ROOT/scripts/lib/ui.sh"
ak_init
MODE="${1:---check}"
case "$MODE" in --check|--apply) ;; *) ak_err 'Mode incorrect'; exit 2 ;; esac
(( EUID == 0 )) || { ak_err 'Exécuter avec sudo'; exit 1; }
command -v ansible-playbook >/dev/null 2>&1 || { ak_err 'Ansible absent'; exit 1; }
export ANSIBLE_CONFIG="$ROOT/ansible/ansible.cfg"
export ANSIBLE_CALLBACK_PLUGINS="$ROOT/ansible/plugins/callback"
export ANSIBLE_STDOUT_CALLBACK='autokom'
export ANSIBLE_NOCOWS=1
if [[ "$MODE" == --check ]]; then
    export AUTOKOM_CHECK_MODE=1
    ansible-playbook \
        -i "$ROOT/ansible/inventories/local/hosts.ini" \
        "$ROOT/ansible/playbooks/foundation.yml" --check
else
    export AUTOKOM_CHECK_MODE=0
    ansible-playbook \
        -i "$ROOT/ansible/inventories/local/hosts.ini" \
        "$ROOT/ansible/playbooks/foundation.yml"
fi
