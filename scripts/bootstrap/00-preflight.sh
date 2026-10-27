#!/usr/bin/env bash
# Ancien chemin compatible. Utiliser scripts/bootstrap/preflight.sh
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec bash "$ROOT/scripts/bootstrap/preflight.sh" "$@"
