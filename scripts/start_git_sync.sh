#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# scripts/start_git_sync.sh — Standalone git auto-push (NOT linked to HANOON)
#
# Runs in its own process. HANOON can be stopped/started freely; git sync keeps
# pushing repo changes using GITHUB_TOKEN from .env.
#
# Checklist (auto-checked on start):
#   ✓ GITHUB_TOKEN in .env
#   ✓ GITHUB_HANOON_REPO (or GITHUB_REPO)
#   ✓ git installed
#
# Usage:
#   ./scripts/start_git_sync.sh
#   GIT_AUTO_PUSH_INTERVAL_SEC=20 ./scripts/start_git_sync.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"

PID_FILE="$LOG_DIR/git_sync.pid"
LOG_FILE="$LOG_DIR/git_sync.log"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export GIT_SYNC_STANDALONE=1
export GH_TOKEN="${GITHUB_TOKEN:-${GITHUB_PAT:-}}"
export GITHUB_TOKEN="${GITHUB_TOKEN:-${GITHUB_PAT:-}}"

if [ -f "$PID_FILE" ]; then
  GPID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$GPID" ] && kill -0 "$GPID" 2>/dev/null; then
    echo "Git sync already running (pid $GPID) — log: $LOG_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

python3 -c "
from core.env_secrets import bootstrap_env
ok, msg = bootstrap_env('$ROOT')
print('Env:', msg)
" 2>/dev/null || true

if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi

echo "Starting standalone git sync daemon..."
nohup python3 "$ROOT/scripts/git_auto_push.py" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✅ Git sync daemon pid $(cat "$PID_FILE")"
  echo "   log: $LOG_FILE"
  echo "   stop: ./scripts/stop_git_sync.sh"
else
  echo "❌ Git sync failed to start — see $LOG_FILE"
  tail -20 "$LOG_FILE" 2>/dev/null || true
  rm -f "$PID_FILE"
  exit 1
fi
