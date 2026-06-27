#!/usr/bin/env bash
# Start Halim serve + standalone Telegram chat (no trading algo required).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

export HALIM_STANDALONE_TELEGRAM=true
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

if [[ -f "$ROOT/requirements.txt" ]]; then
  pip install -q -r "$ROOT/requirements.txt" 2>/dev/null || true
fi

chmod +x "$ROOT/scripts/halim_install_lm.sh" 2>/dev/null || true
"$ROOT/scripts/halim_install_lm.sh" 2>/dev/null || true

python3 -c "
from core.env_secrets import bootstrap_env
bootstrap_env('$ROOT')
" 2>/dev/null || true

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

echo "══════════════════════════════════════════════════════════════"
echo "  M. A. HALIM — standalone (serve + Telegram chat)"
echo "  Serve: ${HALIM_SERVER_URL:-http://127.0.0.1:8765}"
echo "  Logs:  $LOG_DIR/halim_serve.log · halim_telegram.log"
echo "  Stop:  ./scripts/halim_stop.sh  or double-click STOP_HALIM.command"
echo "══════════════════════════════════════════════════════════════"

"$ROOT/scripts/ensure_halim_active.sh" --with-telegram

# Foreground Telegram listener (ensure_halim may have started it in background — prefer foreground here)
if pgrep -f "halim_telegram_standalone.py" >/dev/null 2>&1; then
  echo ""
  echo "💬 Halim Telegram listener running in background."
  echo "   Tail logs: tail -f $LOG_DIR/halim_telegram.log $LOG_DIR/halim_serve.log"
  echo "   Press Ctrl+C to exit this window (Halim keeps running)."
  tail -f "$LOG_DIR/halim_serve.log" "$LOG_DIR/halim_telegram.log" 2>/dev/null || sleep infinity
else
  echo ""
  echo "💬 Starting Halim Telegram in foreground…"
  exec env PYTHONPATH="$ROOT/halim:$ROOT" python "$ROOT/core/halim_telegram_standalone.py"
fi
