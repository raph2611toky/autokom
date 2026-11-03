#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/ui.sh
source "$ROOT/scripts/lib/ui.sh"
ak_init
. /etc/os-release
ak_kv 'Système' "${PRETTY_NAME:-Inconnu}"
ak_kv 'Architecture' "$(uname -m)"
ak_kv 'RAM disponible' "$(free -h | awk '/^Mem:/ {print $7}')"
ak_kv 'Stockage libre' "$(df -h / | awk 'NR==2 {print $4}')"

present=() missing=()
for tool in nginx certbot docker jenkins ansible trivy semgrep nikto; do
    if command -v "$tool" >/dev/null 2>&1; then present+=("$tool"); else missing+=("$tool"); fi
done
ak_kv 'Déjà présents' "${present[*]:-aucun}"
ak_kv 'À examiner' "${missing[*]:-aucun}"
if command -v ss >/dev/null 2>&1 && ss -lnt | grep -Eq ':(80|443)([[:space:]]|$)'; then
    ak_info 'Ports 80/443 utilisés : configurations Nginx existantes préservées.'
fi
ak_ok 'Préflight terminé, aucune configuration modifiée.'
