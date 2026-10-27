#!/usr/bin/env bash
# Ancien chemin compatible. Utiliser scripts/install/services.sh
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec bash "$ROOT/scripts/install/services.sh" "$@"
