#!/usr/bin/env bash
# Stop Halim serve and/or standalone Telegram listener.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
SERVE_PID="$LOG_DIR/halim_serve.pid"
TG_PID="$LOG_DIR/halim_telegram.pid"

TELEGRAM_ONLY=false
SERVE_ONLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --telegram-only) TELEGRAM_ONLY=true; shift ;;
    --serve-only) SERVE_ONLY=true; shift ;;
    *) shift ;;
  esac
done

_stop_pid_file() {
  local file="$1"
  local label="$2"
  if [[ -f "$file" ]]; then
    local pid
    pid=$(tr -d '[:space:]' <"$file" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "🛑 Stopping $label (pid $pid)…"
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi
}

_stop_pgrep() {
  local pattern="$1"
  local label="$2"
  local pids
  pids=$(pgrep -f "$pattern" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "🛑 Stopping $label…"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

if [[ "$TELEGRAM_ONLY" != "true" ]] && [[ "$SERVE_ONLY" != "true" ]]; then
  _stop_pid_file "$TG_PID" "Halim Telegram"
  _stop_pgrep "halim_telegram_standalone.py" "Halim Telegram"
  _stop_pid_file "$SERVE_PID" "Halim serve"
  _stop_pgrep "halim/halim/serve.py" "Halim serve"
  echo "✅ Halim stopped"
elif [[ "$TELEGRAM_ONLY" == "true" ]]; then
  _stop_pid_file "$TG_PID" "Halim Telegram"
  _stop_pgrep "halim_telegram_standalone.py" "Halim Telegram"
  echo "✅ Halim Telegram stopped"
else
  _stop_pid_file "$SERVE_PID" "Halim serve"
  _stop_pgrep "halim/halim/serve.py" "Halim serve"
  echo "✅ Halim serve stopped"
fi
