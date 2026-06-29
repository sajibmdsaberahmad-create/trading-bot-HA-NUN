#!/usr/bin/env bash
# Stop Halim serve watchdog (does not stop Halim serve itself).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="$LOG_DIR/halim_watchdog.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Halim watchdog not running"
  exit 0
fi

WPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
if [[ -z "$WPID" ]] || ! kill -0 "$WPID" 2>/dev/null; then
  echo "Halim watchdog not running"
  rm -f "$PID_FILE"
  exit 0
fi

echo "Stopping Halim watchdog (pid $WPID)…"
kill -TERM "$WPID" 2>/dev/null || true
sleep 2
kill -KILL "$WPID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "✅ Halim watchdog stopped (Halim serve left running)"
