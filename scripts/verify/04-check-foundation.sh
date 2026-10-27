#!/usr/bin/env bash
# Ancien chemin compatible. Utiliser scripts/verify/foundation.sh
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec bash "$ROOT/scripts/verify/foundation.sh" "$@"
