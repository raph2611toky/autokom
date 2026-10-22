#!/usr/bin/env bash
set -Eeuo pipefail

printf '\n=== AUTOKOM / PREFLIGHT ===\n'
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  printf 'OS: %s\n' "${PRETTY_NAME:-unknown}"
fi
printf '\nResources:\n'
free -h
df -h /
printf '\nListeners (80, 443, 8080, 3000, 9090):\n'
if command -v ss >/dev/null 2>&1; then
  ss -lntp | grep -E ':(80|443|8080|3000|9090)([[:space:]]|$)' || true
fi
printf '\nInstalled services/tools:\n'
for program in nginx certbot docker jenkins trivy semgrep nikto ansible; do
  if command -v "$program" >/dev/null 2>&1; then
    printf '  [present] %s (%s)\n' "$program" "$(command -v "$program")"
  else
    printf '  [absent ] %s\n' "$program"
  fi
done
printf '\nNo modifications were made.\n'
