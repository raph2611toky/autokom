#!/usr/bin/env bash
# AUTOKOM Jenkins & databases. Interactive or explicit --non-interactive options.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec python3 "$ROOT/scripts/lib/services.py" "$@"
