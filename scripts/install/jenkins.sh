#!/usr/bin/env bash
# AUTOKOM Jenkins post-install; all arguments passed through.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec python3 "$ROOT/scripts/lib/jenkins_postinstall.py" "$@"
