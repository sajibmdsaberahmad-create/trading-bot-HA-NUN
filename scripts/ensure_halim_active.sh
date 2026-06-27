#!/usr/bin/env bash
# Ensure Halim serve is running (health on :8765). Optionally start standalone Telegram.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

LOG_DIR="${LOG_DIR:-$ROOT/logs}"
SERVE_PID="$LOG_DIR/halim_serve.pid"
SERVE_LOG="$LOG_DIR/halim_serve.log"
TG_PID="$LOG_DIR/halim_telegram.pid"
TG_LOG="$LOG_DIR/halim_telegram.log"
HALIM_URL="${HALIM_SERVER_URL:-http://127.0.0.1:8765}"

SERVE_ONLY=false
WITH_TELEGRAM=false
WAIT_SEC="${HALIM_STARTUP_WAIT_SEC:-120}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serve-only) SERVE_ONLY=true; shift ;;
    --with-telegram) WITH_TELEGRAM=true; shift ;;
    *) shift ;;
  esac
done

if [[ "$SERVE_ONLY" == "true" ]]; then
  WITH_TELEGRAM=false
elif [[ "${HALIM_STANDALONE_TELEGRAM:-false}" == "true" ]]; then
  WITH_TELEGRAM=true
fi

mkdir -p "$LOG_DIR"

_ensure_checkpoint() {
  local ckpt="$ROOT/halim/data/checkpoints/toddler_v1"
  if [[ -f "$ckpt/merged/model.safetensors" ]] || [[ -f "$ckpt/lora_adapter/adapter_model.safetensors" ]]; then
    return 0
  fi
  local zip="${HALIM_TODDLER_ZIP:-$HOME/Downloads/halim_toddler_v1.zip}"
  if [[ -f "$zip" ]]; then
    echo "📦 Halim toddler checkpoint missing — extracting $zip…"
    mkdir -p "$ROOT/halim/data/checkpoints"
    unzip -o "$zip" -d "$ROOT/halim/data/checkpoints/"
    if [[ -d "$ROOT/venv" ]]; then
      # shellcheck disable=SC1091
      source "$ROOT/venv/bin/activate"
    fi
    "$ROOT/scripts/halim_register_checkpoint.sh" toddler_v1 --backend "${HALIM_LM_BACKEND:-mlx}" 2>/dev/null || true
  else
    echo "⚠️  No Halim toddler checkpoint — LM may be unavailable."
    echo "    Run: ./scripts/halim_start_toddler.sh  or set HALIM_TODDLER_ZIP=/path/to/halim_toddler_v1.zip"
  fi
}

_halim_health() {
  curl -sf --max-time 2 "${HALIM_URL%/}/health" >/dev/null 2>&1
}

_serve_running() {
  if [[ -f "$SERVE_PID" ]]; then
    local pid
    pid=$(tr -d '[:space:]' <"$SERVE_PID" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  pgrep -f "halim/halim/serve.py" >/dev/null 2>&1
}

_start_serve() {
  if [[ -d "$ROOT/venv" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/venv/bin/activate"
  fi
  echo "🧠 Starting Halim serve → $HALIM_URL (log: $SERVE_LOG)…"
  nohup "$ROOT/scripts/halim_serve.sh" >>"$SERVE_LOG" 2>&1 &
  echo $! >"$SERVE_PID"
}

_wait_health() {
  local i=0
  while [[ $i -lt $WAIT_SEC ]]; do
    if _halim_health; then
      echo "✅ Halim serve active at $HALIM_URL"
      return 0
    fi
    sleep 2
    i=$((i + 2))
    if (( i % 10 == 0 )); then
      echo "   … waiting for Halim model load (${i}s / ${WAIT_SEC}s)"
    fi
  done
  echo "⚠️  Halim serve did not respond on /health within ${WAIT_SEC}s — check $SERVE_LOG"
  return 1
}

_telegram_running() {
  if [[ -f "$TG_PID" ]]; then
    local pid
    pid=$(tr -d '[:space:]' <"$TG_PID" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  pgrep -f "halim_telegram_standalone.py" >/dev/null 2>&1
}

_start_telegram() {
  if pgrep -f "main.py --mode (scalper|replay-live)" >/dev/null 2>&1; then
    echo "ℹ️  HANOON/replay owns Telegram — skipping standalone Halim listener"
    return 0
  fi
  if [[ -d "$ROOT/venv" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/venv/bin/activate"
  fi
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi
  if [[ -z "${TRADING_BOT_TELEGRAM_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}" ]]; then
    echo "⚠️  No TELEGRAM token — Halim Telegram chat disabled (set TRADING_BOT_TELEGRAM_TOKEN in .env)"
    return 1
  fi
  echo "💬 Starting Halim Telegram listener (log: $TG_LOG)…"
  nohup env PYTHONPATH="$ROOT/halim:$ROOT" python "$ROOT/core/halim_telegram_standalone.py" >>"$TG_LOG" 2>&1 &
  echo $! >"$TG_PID"
  sleep 1
  echo "✅ Halim Telegram listener started — message the bot after /verify"
}

_ensure_lm_deps() {
  if [[ -d "$ROOT/halim/data/checkpoints/latest" ]] || [[ -d "$ROOT/halim/data/checkpoints/toddler_v1" ]]; then
    chmod +x "$ROOT/scripts/halim_install_lm.sh" 2>/dev/null || true
    "$ROOT/scripts/halim_install_lm.sh" 2>/dev/null || {
      echo "⚠️  Halim LM deps missing — run: ./scripts/halim_install_lm.sh"
    }
  fi
}

_ensure_checkpoint
_ensure_lm_deps

if _halim_health; then
  echo "✅ Halim already active at $HALIM_URL"
elif _serve_running; then
  echo "⏳ Halim serve starting — waiting for /health…"
  _wait_health || true
else
  _start_serve
  _wait_health || true
fi

if [[ "$WITH_TELEGRAM" == "true" ]] && ! _telegram_running; then
  _start_telegram || true
fi
