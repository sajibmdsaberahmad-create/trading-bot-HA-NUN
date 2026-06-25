#!/usr/bin/env bash
# Graceful HANOON stop: shutdown file + SIGTERM → git sync + IB disconnect → Ollama unload
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="${PID_FILE:-$LOG_DIR/hanoon.pid}"
SHUTDOWN_FILE="${HANOON_SHUTDOWN_FILE:-$ROOT/runtime/shutdown.request}"
WAIT_SEC="${SHUTDOWN_WAIT_SEC:-120}"

echo "🛑 Graceful HANOON shutdown (up to ${WAIT_SEC}s for session close + git sync)..."

# ── 1. Ask the bot to stop cleanly (works even when blocked in IB calls) ─────
mkdir -p "$(dirname "$SHUTDOWN_FILE")"
if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
  python3 -c "
from core.shutdown_control import request_shutdown
request_shutdown('stop_hanoon.sh')
print('   Shutdown request written')
" 2>/dev/null || touch "$SHUTDOWN_FILE"
else
  touch "$SHUTDOWN_FILE"
fi

# ── 2. SIGTERM the Python process (runs _shutdown: report, git push, IB disconnect) ─
HPID=""
if [ -f "$PID_FILE" ]; then
  HPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
fi

if [ -n "$HPID" ] && kill -0 "$HPID" 2>/dev/null; then
  echo "   Sending SIGTERM to HANOON pid $HPID..."
  kill -TERM "$HPID" 2>/dev/null || true
else
  echo "   PID file missing or stale — searching for scalper process..."
  pkill -TERM -f "main.py --mode scalper" 2>/dev/null || true
  HPID=$(pgrep -f "main.py --mode scalper" 2>/dev/null | head -1 || true)
fi

elapsed=0
while [ -n "$HPID" ] && kill -0 "$HPID" 2>/dev/null && [ "$elapsed" -lt "$WAIT_SEC" ]; do
  sleep 2
  elapsed=$((elapsed + 2))
  if [ $((elapsed % 10)) -eq 0 ]; then
    echo "   Waiting for graceful shutdown... ${elapsed}s"
  fi
done

if [ -n "$HPID" ] && kill -0 "$HPID" 2>/dev/null; then
  echo "⚠️  Bot still running after ${WAIT_SEC}s — sending SIGKILL"
  kill -KILL "$HPID" 2>/dev/null || true
  sleep 1
  pkill -KILL -f "main.py --mode scalper" 2>/dev/null || true
fi

rm -f "$PID_FILE" "$SHUTDOWN_FILE"

# ── 3. Stop Ollama server if we started it ───────────────────────────────────
if [ -f "$LOG_DIR/ollama.pid" ]; then
  OPID=$(cat "$LOG_DIR/ollama.pid" 2>/dev/null || true)
  if [ -n "$OPID" ] && kill -0 "$OPID" 2>/dev/null; then
    echo "   Stopping Ollama server (pid $OPID)..."
    kill "$OPID" 2>/dev/null || true
  fi
  rm -f "$LOG_DIR/ollama.pid"
fi

# ── 4. Fallback local cleanup if bot did not finish shutdown hook ────────────
if [ -d "$ROOT/venv" ]; then
  python3 -c "
from core.local_cleanup import cleanup_local_workspace
cleanup_local_workspace(aggressive=True)
" 2>/dev/null || true
fi

# ── 5. Free RAM: unload Ollama models ────────────────────────────────────────
if command -v ollama >/dev/null 2>&1; then
  echo "   Unloading Ollama models from RAM..."
  ollama ps -q 2>/dev/null | while read -r name; do
    [ -n "$name" ] && ollama stop "$name" 2>/dev/null || true
  done
  for m in qwen2.5:3b qwen2.5:1.5b phi3:mini llama3.2:3b qwen2.5:0.5b llama3; do
    ollama stop "$m" 2>/dev/null || true
  done
fi

echo "✅ HANOON stopped gracefully (session report + git sync attempted)"
echo "   Tip: use ./stop.sh or double-click STOP.command — not Ctrl+C in the bot terminal"
