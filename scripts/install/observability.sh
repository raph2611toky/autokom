#!/usr/bin/env bash
# AUTOKOM · Phase 3 — Observability
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec python3 "$ROOT/scripts/lib/observability.py" "$@"
