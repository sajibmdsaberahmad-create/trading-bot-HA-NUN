#!/usr/bin/env bash
# Graceful HANOON stop: shutdown file + SIGTERM → git sync + IB disconnect
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

# ── 2. Collect all scalper PIDs (file + process search) ─────────────────────
PIDS=()
if [ -f "$PID_FILE" ]; then
  HPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
  if [ -n "$HPID" ] && kill -0 "$HPID" 2>/dev/null; then
    PIDS+=("$HPID")
  fi
fi
while IFS= read -r pid; do
  [ -n "$pid" ] || continue
  skip=0
  for existing in "${PIDS[@]:-}"; do
    [ "$existing" = "$pid" ] && skip=1 && break
  done
  [ "$skip" -eq 1 ] || PIDS+=("$pid")
done < <(pgrep -f "main.py --mode scalper" 2>/dev/null || true)

if [ ${#PIDS[@]} -eq 0 ]; then
  echo "   No running HANOON scalper found"
  rm -f "$PID_FILE" "$SHUTDOWN_FILE"
  exit 0
fi

echo "   Sending SIGTERM to: ${PIDS[*]}"
for pid in "${PIDS[@]}"; do
  kill -TERM "$pid" 2>/dev/null || true
done

elapsed=0
while [ "$elapsed" -lt "$WAIT_SEC" ]; do
  alive=0
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      alive=1
      break
    fi
  done
  [ "$alive" -eq 0 ] && break
  sleep 2
  elapsed=$((elapsed + 2))
  if [ $((elapsed % 10)) -eq 0 ]; then
    echo "   Waiting for graceful shutdown... ${elapsed}s"
  fi
done

still_alive=()
for pid in "${PIDS[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    still_alive+=("$pid")
  fi
done

if [ ${#still_alive[@]} -gt 0 ]; then
  echo "⚠️  Bot still running after ${WAIT_SEC}s — sending SIGKILL to ${still_alive[*]}"
  for pid in "${still_alive[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  sleep 1
  pkill -KILL -f "main.py --mode scalper" 2>/dev/null || true
fi

rm -f "$PID_FILE" "$SHUTDOWN_FILE"

# ── 3. Fallback local cleanup if bot did not finish shutdown hook ────────────
if [ -d "$ROOT/venv" ]; then
  python3 -c "
from core.local_cleanup import cleanup_local_workspace
cleanup_local_workspace(aggressive=True)
" 2>/dev/null || true
fi

echo "✅ HANOON stopped gracefully (session report + git sync attempted)"
echo "   Tip: use ./stop.sh or double-click STOP.command — not Ctrl+C in the bot terminal"
