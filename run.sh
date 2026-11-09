#!/usr/bin/env bash
# Start the pueue task dashboard. Extra flags are passed through, e.g.:
#   ./run.sh --port 8766 --refresh 10
#   ./run.sh --check
set -euo pipefail
cd "$(dirname "$0")"

: "${PUEUE_PORT:=6924}"

exec python3 -m pueue_viz --port $PUEUE_PORT "$@"
