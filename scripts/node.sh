#!/usr/bin/env bash
# Start one GHOST python edge node. All arguments pass through to node.main.
#   scripts/node.sh --cam cam-01 --source 0
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at .venv/ — see README.md"; exit 1; }
cd "$ROOT"
exec "$PY" -m node.main "$@"
