#!/usr/bin/env bash
# stop.sh — One-command stop: scalper + Halim serve + git + evolution
set -euo pipefail
cd "$(dirname "$0")"

echo "🛑 Stopping everything..."

# 1. Graceful HANOON scalper shutdown (handles gold + evolution + git)
bash scripts/stop_hanoon.sh 2>/dev/null || true

# 2. Kill Halim serve
HALIM_PID=$(pgrep -f "halim/halim/serve.py" 2>/dev/null || true)
if [ -n "$HALIM_PID" ]; then
  echo "   Stopping Halim serve (PID $HALIM_PID)..."
  kill -TERM "$HALIM_PID" 2>/dev/null || true
  sleep 1
  kill -0 "$HALIM_PID" 2>/dev/null && kill -KILL "$HALIM_PID" 2>/dev/null || true
  echo "   ✅ Halim serve stopped"
fi

# 3. Cleanup pid file
rm -f logs/hanoon.pid runtime/shutdown.request 2>/dev/null || true

echo "✅ All stopped. Run ./start.sh to restart."
