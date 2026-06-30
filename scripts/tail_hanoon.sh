#!/usr/bin/env bash
# Live tail for HANOON — use in macOS Terminal or Cursor terminal (blocks until Ctrl+C)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${HANOON_LOG_PATH:-$ROOT/logs/HANOON.log}"
if [[ ! -f "$LOG" ]]; then
  echo "Log not found: $LOG" >&2
  exit 1
fi
echo "Tailing: $LOG  (Ctrl+C to stop)"
exec tail -f "$LOG"
