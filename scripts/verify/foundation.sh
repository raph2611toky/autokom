#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/ui.sh
source "$ROOT/scripts/lib/ui.sh"
ak_init
required=(git curl nginx certbot ansible-playbook trivy semgrep nikto)
errors=0
for tool in "${required[@]}"; do
    if command -v "$tool" >/dev/null 2>&1; then
        ak_ok "$tool"
    else
        ak_err "Manquant : $tool"
        errors=$((errors + 1))
    fi
done
if command -v nginx >/dev/null 2>&1; then
    if nginx -t >/dev/null 2>&1; then ak_ok 'Syntaxe Nginx valide'; else ak_err 'Syntaxe Nginx invalide'; errors=$((errors + 1)); fi
fi
if (( errors > 0 )); then
    ak_warn "$errors contrôle(s) en échec : consulter le journal et corriger avant la suite."
    exit 1
fi
ak_ok 'Fondation vérifiée.'
