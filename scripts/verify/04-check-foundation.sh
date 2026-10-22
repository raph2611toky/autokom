#!/usr/bin/env bash
set -Eeuo pipefail

echo '=== AUTOKOM | verification CLI ==='
for c in ansible-playbook nginx certbot git curl jq openssl nslookup \
         nmap nikto lynis semgrep trivy shellcheck; do
  if command -v "$c" >/dev/null 2>&1; then
    printf '[OK]     %s\n' "$c"
  else
    printf '[ABSENT] %s\n' "$c"
  fi
done
if command -v nginx >/dev/null 2>&1; then
  echo '--- Test configuration Nginx ---'
  sudo nginx -t
fi
if command -v trivy >/dev/null 2>&1; then trivy --version; fi
if command -v semgrep >/dev/null 2>&1; then semgrep --version; fi
echo 'Note: aucun scan distant ni aucune modification de pare-feu n est effectue.'
