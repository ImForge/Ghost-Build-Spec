#!/usr/bin/env bash
# GHOST test suite: unit tests, then the end-to-end smoke run.
# Smoke needs the broker and brain already running (scripts/dev.sh).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at .venv/ — see README.md"; exit 1; }
cd "$ROOT"

FAIL=0
echo "=== pytest ==="
"$PY" -m pytest brain/tests node/tests -q || FAIL=1

echo
echo "=== smoke (needs broker + brain) ==="
if "$PY" -c "import socket,os,sys; s=socket.socket(); s.settimeout(1.5); sys.exit(s.connect_ex((os.getenv('BROKER_HOST','127.0.0.1'), int(os.getenv('BROKER_PORT','1883')))))" 2>/dev/null; then
  "$PY" tools/smoke.py || FAIL=1
else
  echo "broker not reachable — skipping smoke. Start it with scripts/dev.sh"
  FAIL=1
fi

echo
[ "$FAIL" -eq 0 ] && echo "ALL GREEN" || echo "FAILURES — see above"
exit $FAIL
