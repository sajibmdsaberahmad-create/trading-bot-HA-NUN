#!/usr/bin/env bash
# Graceful HANOON stop: SIGTERM → wait for git push + cleanup → unload Ollama
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="$LOG_DIR/hanoon.pid"
WAIT_SEC="${SHUTDOWN_WAIT_SEC:-120}"

echo "Stopping HANOON scalper (graceful, up to ${WAIT_SEC}s for git sync)..."

if [ -f "$PID_FILE" ]; then
  HPID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$HPID" ] && kill -0 "$HPID" 2>/dev/null; then
    kill -TERM "$HPID" 2>/dev/null || true
    elapsed=0
    while kill -0 "$HPID" 2>/dev/null && [ "$elapsed" -lt "$WAIT_SEC" ]; do
      sleep 2
      elapsed=$((elapsed + 2))
    done
    if kill -0 "$HPID" 2>/dev/null; then
      echo "⚠️  Bot still running after ${WAIT_SEC}s — force kill"
      kill -KILL "$HPID" 2>/dev/null || true
    fi
  fi
else
  pkill -TERM -f "main.py --mode scalper" 2>/dev/null || true
  sleep 5
  pkill -KILL -f "main.py --mode scalper" 2>/dev/null || true
fi

rm -f "$PID_FILE"

if [ -f "$LOG_DIR/ollama.pid" ]; then
  OPID=$(cat "$LOG_DIR/ollama.pid" 2>/dev/null || true)
  if [ -n "$OPID" ] && kill -0 "$OPID" 2>/dev/null; then
    echo "Stopping Ollama server (pid $OPID)..."
    kill "$OPID" 2>/dev/null || true
  fi
  rm -f "$LOG_DIR/ollama.pid"
fi

# Fallback local cleanup if bot did not finish shutdown hook
if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
  python3 -c "
from core.local_cleanup import cleanup_local_workspace
cleanup_local_workspace(aggressive=True)
" 2>/dev/null || true
fi

# Free RAM: unload all Ollama models from memory
if command -v ollama >/dev/null 2>&1; then
  echo "Unloading Ollama models from RAM..."
  ollama ps -q 2>/dev/null | while read -r name; do
    [ -n "$name" ] && ollama stop "$name" 2>/dev/null || true
  done
  for m in qwen2.5:3b qwen2.5:1.5b phi3:mini llama3.2:3b qwen2.5:0.5b llama3; do
    ollama stop "$m" 2>/dev/null || true
  done
fi

echo "✅ HANOON stopped (repos synced + local cleanup)"
