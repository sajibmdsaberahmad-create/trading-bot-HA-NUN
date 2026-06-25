#!/usr/bin/env bash
# Stop standalone git sync daemon (does not affect HANOON)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="$LOG_DIR/git_sync.pid"
WAIT_SEC="${GIT_SYNC_STOP_WAIT_SEC:-30}"

if [ ! -f "$PID_FILE" ]; then
  echo "Git sync daemon not running (no pid file)"
  exit 0
fi

GPID=$(cat "$PID_FILE" 2>/dev/null || true)
if [ -z "$GPID" ] || ! kill -0 "$GPID" 2>/dev/null; then
  echo "Git sync daemon not running"
  rm -f "$PID_FILE"
  exit 0
fi

echo "Stopping git sync daemon (pid $GPID)..."
kill -TERM "$GPID" 2>/dev/null || true
elapsed=0
while kill -0 "$GPID" 2>/dev/null && [ "$elapsed" -lt "$WAIT_SEC" ]; do
  sleep 1
  elapsed=$((elapsed + 1))
done
if kill -0 "$GPID" 2>/dev/null; then
  kill -KILL "$GPID" 2>/dev/null || true
fi
rm -f "$PID_FILE"
echo "✅ Git sync stopped"
