#!/usr/bin/env bash
# Lien global, uniquement sur demande explicite, sans modifier les services.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TARGET=/usr/local/bin/autokom
(( EUID == 0 )) || { echo 'Utiliser sudo.' >&2; exit 1; }
[[ -x "$ROOT/autokom" ]] || { echo 'Lanceur AUTOKOM absent.' >&2; exit 1; }
if [[ -L "$TARGET" && "$(readlink -f "$TARGET")" == "$ROOT/autokom" ]]; then
    printf '  ✓ autokom déjà disponible : %s\n' "$TARGET"
    exit 0
fi
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
    printf '  ✗ %s appartient déjà à une autre installation ; aucune modification.\n' "$TARGET" >&2
    exit 1
fi
install -d -m 755 /usr/local/bin
ln -s -- "$ROOT/autokom" "$TARGET"
printf '  ✓ Commande installée : sudo autokom install\n'
