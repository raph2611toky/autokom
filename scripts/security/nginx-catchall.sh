#!/usr/bin/env bash
set -Eeuo pipefail
# Usage: sudo bash scripts/security/03-nginx-catchall.sh [--check|--apply]
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:---check}"
AVAILABLE=/etc/nginx/sites-available/00-catchall.conf
ENABLED=/etc/nginx/sites-enabled/00-catchall.conf
SOURCE="$ROOT/config/nginx/00-catchall.conf"

if [[ "$MODE" != '--check' && "$MODE" != '--apply' ]]; then
  echo 'Usage: sudo bash scripts/security/03-nginx-catchall.sh [--check|--apply]' >&2
  exit 2
fi
if [[ "${EUID}" -ne 0 ]]; then
  echo '[ERREUR] Executer avec sudo' >&2
  exit 1
fi
if ! command -v nginx >/dev/null 2>&1; then
  echo '[ERREUR] Nginx absent. Installer la foundation avant.' >&2
  exit 1
fi
nginx -t

# Cas du serveur existant: NE RIEN MODIFIER si le catch-all est deja actif.
if [[ -e "$ENABLED" ]] && grep -Eq 'return[[:space:]]+444;' "$ENABLED" && \
     grep -Eq 'ssl_reject_handshake[[:space:]]+on;' "$ENABLED"; then
  echo '[OK] Catch-all HTTP/HTTPS deja present. Aucun changement.'
  exit 0
fi

# Il ne peut y avoir qu'un seul default_server par adresse/port.
# Le script refuse de remplacer les declarations existantes automatiquement.
if nginx -T 2>/dev/null | sed 's/[[:space:]]*#.*$//' | \
   grep -Eq '^[[:space:]]*listen[[:space:]]+([^;]*)(80|443)[^;]*default_server'; then
  echo '[STOP] Un default_server existe deja. Aucune modification.' >&2
  echo 'Inspecter: sudo nginx -T | grep -n default_server' >&2
  exit 3
fi

if [[ -e "$AVAILABLE" || -L "$ENABLED" || -e "$ENABLED" ]]; then
  echo '[STOP] Le chemin 00-catchall est deja utilise. Aucun ecrasement.' >&2
  exit 3
fi
if [[ "$MODE" == '--check' ]]; then
  echo '[PLAN] Pret a creer et activer le catch-all. Relancer avec --apply.'
  exit 0
fi

install -m 0644 "$SOURCE" "$AVAILABLE"
ln -s "$AVAILABLE" "$ENABLED"
if nginx -t; then
  systemctl reload nginx
  echo '[OK] Catch-all installe et Nginx recharge.'
else
  echo '[ERREUR] Test nginx invalide: retour arriere.' >&2
  rm -f "$ENABLED" "$AVAILABLE"
  nginx -t || true
  exit 1
fi
