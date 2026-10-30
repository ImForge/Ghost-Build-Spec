#!/usr/bin/env bash
# GHOST dev stack: broker + brain + dashboard. Ctrl-C stops all three.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at .venv/ — run: python -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"; exit 1; }

# --- broker: docker first, native mosquitto as fallback (see docs/DECISIONS.md D4)
BROKER_PID=""
if docker info >/dev/null 2>&1; then
  echo "[dev] starting mosquitto via docker compose"
  docker compose up -d mosquitto
elif command -v mosquitto >/dev/null 2>&1; then
  echo "[dev] no docker daemon — starting native mosquitto"
  mosquitto -c "$ROOT/mosquitto/mosquitto.conf" &
  BROKER_PID=$!
else
  echo "[dev] no docker and no mosquitto on PATH — install one of them"; exit 1
fi

PIDS=()
cleanup() {
  echo; echo "[dev] stopping"
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  [ -n "$BROKER_PID" ] && kill "$BROKER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev] brain    → http://localhost:8000  (docs at /docs, web node at /node)"
"$PY" -m uvicorn brain.app:app --host 0.0.0.0 --port "${BRAIN_PORT:-8000}" &
PIDS+=($!)

if [ -d "$ROOT/dashboard/node_modules" ]; then
  echo "[dev] dashboard → http://localhost:3000"
  ( cd "$ROOT/dashboard" && npm run dev ) &
  PIDS+=($!)
else
  echo "[dev] dashboard skipped — run: cd dashboard && npm install"
fi

wait
