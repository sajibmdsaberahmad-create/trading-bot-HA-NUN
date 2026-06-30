#!/usr/bin/env bash
# Start standalone IB Gateway port watchdog (monitors API socket during HANOON).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR" "$ROOT/runtime"
PID_FILE="$LOG_DIR/ib_gateway_watchdog.pid"
LOG_FILE="$LOG_DIR/ib_gateway_watchdog.log"

if [[ -f "$PID_FILE" ]]; then
  WPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
  if [[ -n "$WPID" ]] && kill -0 "$WPID" 2>/dev/null; then
    echo "IB Gateway watchdog already running (pid $WPID)"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export IB_HOST="${IB_HOST:-127.0.0.1}"
export IB_PORT="${IB_PORT:-4002}"

echo "Starting IB Gateway watchdog…"
nohup python3 "$ROOT/scripts/ib_gateway_watchdog.py" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✅ IB Gateway watchdog pid $(cat "$PID_FILE") — monitors ${IB_HOST}:${IB_PORT}"
  echo "   log: $LOG_FILE"
  echo "   stop: ./scripts/stop_ib_gateway_watchdog.sh"
else
  echo "❌ IB Gateway watchdog failed — see $LOG_FILE"
  tail -10 "$LOG_FILE" 2>/dev/null || true
  rm -f "$PID_FILE"
  exit 1
fi
