#!/usr/bin/env bash
# Compatibilité : préférer scripts/security/nginx-catchall.sh
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec bash "$ROOT/scripts/security/nginx-catchall.sh" "$@"
