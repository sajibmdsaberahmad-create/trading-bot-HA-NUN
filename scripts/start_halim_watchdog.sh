#!/usr/bin/env bash
# Start standalone Halim serve watchdog (keeps :8765 alive during HANOON/replay).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"
PID_FILE="$LOG_DIR/halim_watchdog.pid"
LOG_FILE="$LOG_DIR/halim_watchdog.log"

if [[ -f "$PID_FILE" ]]; then
  WPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
  if [[ -n "$WPID" ]] && kill -0 "$WPID" 2>/dev/null; then
    echo "Halim watchdog already running (pid $WPID)"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
echo "Starting Halim serve watchdog…"
nohup python3 "$ROOT/scripts/halim_serve_watchdog.py" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✅ Halim watchdog pid $(cat "$PID_FILE") — keeps :8765 alive during trading"
  echo "   log: $LOG_FILE"
  echo "   stop: ./scripts/stop_halim_watchdog.sh"
else
  echo "❌ Halim watchdog failed — see $LOG_FILE"
  tail -10 "$LOG_FILE" 2>/dev/null || true
  rm -f "$PID_FILE"
  exit 1
fi
