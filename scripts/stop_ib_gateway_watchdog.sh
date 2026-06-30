#!/usr/bin/env bash
# Stop IB Gateway port watchdog (does not stop IB Gateway or HANOON).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="$LOG_DIR/ib_gateway_watchdog.pid"
DOWN_FLAG="${RUNTIME_DIR:-$ROOT/runtime}/ib_gateway_down.flag"

if [[ ! -f "$PID_FILE" ]]; then
  echo "IB Gateway watchdog not running"
  rm -f "$DOWN_FLAG" 2>/dev/null || true
  exit 0
fi

WPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
if [[ -z "$WPID" ]] || ! kill -0 "$WPID" 2>/dev/null; then
  echo "IB Gateway watchdog not running"
  rm -f "$PID_FILE" "$DOWN_FLAG"
  exit 0
fi

echo "Stopping IB Gateway watchdog (pid $WPID)…"
kill -TERM "$WPID" 2>/dev/null || true
sleep 2
kill -KILL "$WPID" 2>/dev/null || true
rm -f "$PID_FILE" "$DOWN_FLAG"
echo "✅ IB Gateway watchdog stopped"
