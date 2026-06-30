#!/usr/bin/env bash
# Graceful HANOON (live) shutdown — Halim gold + evolution + git sync + IB cleanup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="${PID_FILE:-$LOG_DIR/hanoon.pid}"
SHUTDOWN_FILE="${HANOON_SHUTDOWN_FILE:-$ROOT/runtime/shutdown.request}"
WAIT_SEC="${SHUTDOWN_WAIT_SEC:-180}"
export PYTHONPATH="${ROOT}/halim:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

echo "🛑 Graceful HANOON shutdown (up to ${WAIT_SEC}s for evolution + git sync)…"

mkdir -p "$(dirname "$SHUTDOWN_FILE")"
if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

python3 -c "
from core.shutdown_control import request_shutdown
request_shutdown('stop_hanoon.sh')
print('   Shutdown request written')
" 2>/dev/null || touch "$SHUTDOWN_FILE"

python3 -c "
from core.config import BotConfig
from core.learning_persistence import fsync_critical_artifacts, snapshot_learning
fsync_critical_artifacts()
snapshot_learning(BotConfig(), trigger='pre_stop_live', halim_export=True)
" 2>/dev/null || true

# Stop sidecars first so they cannot respawn or compete during teardown.
"$ROOT/scripts/stop_git_sync.sh" 2>/dev/null || true
"$ROOT/scripts/stop_halim_watchdog.sh" 2>/dev/null || true
"$ROOT/scripts/stop_ib_gateway_watchdog.sh" 2>/dev/null || true

PIDS=()
_add_pid() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  local i
  for i in "${PIDS[@]:-}"; do
    [ "$i" = "$pid" ] && return 0
  done
  if ps -p "$pid" -o args= 2>/dev/null | grep -Eiq 'main\.py.*scalper'; then
    PIDS+=("$pid")
  fi
}

if [ -f "$PID_FILE" ]; then
  _add_pid "$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)"
fi

while IFS= read -r pid; do
  _add_pid "$pid"
done < <(pgrep -f 'main.py --mode scalper' 2>/dev/null || true)

while IFS= read -r pid; do
  _add_pid "$pid"
done < <(pgrep -f 'main.py.*scalper' 2>/dev/null || true)

if [ ${#PIDS[@]} -eq 0 ]; then
  echo "   No running HANOON scalper — running standalone data flush…"
  python3 -c "
from core.graceful_shutdown import run_standalone_shutdown_flush
run_standalone_shutdown_flush(replay=False)
" 2>/dev/null || true
  CLIENT_ID="${CLIENT_ID:-${IB_CLIENT_ID:-1}}"
  python3 "$ROOT/scripts/guard_ib_client_id.py" --client-id "$CLIENT_ID" --release 2>/dev/null || true
  rm -f "$PID_FILE" "$SHUTDOWN_FILE"
  echo "✅ HANOON data flush complete (no live process)"
  exit 0
fi

echo "   Sending SIGTERM to HANOON: ${PIDS[*]}"
for pid in "${PIDS[@]}"; do
  kill -TERM "$pid" 2>/dev/null || true
done

FORCED_KILL=false
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
  if [ $((elapsed % 15)) -eq 0 ]; then
    echo "   Waiting for live teardown (Halim gold + evolution + git)… ${elapsed}s"
  fi
done

still_alive=()
for pid in "${PIDS[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    still_alive+=("$pid")
  fi
done

if [ ${#still_alive[@]} -gt 0 ]; then
  FORCED_KILL=true
  echo "⚠️  HANOON still running after ${WAIT_SEC}s — SIGKILL ${still_alive[*]}"
  for pid in "${still_alive[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  sleep 1
  pkill -KILL -f 'main.py.*scalper' 2>/dev/null || true
  echo "   Running fallback data flush…"
  python3 -c "
from core.graceful_shutdown import run_standalone_shutdown_flush
run_standalone_shutdown_flush(replay=False)
" 2>/dev/null || true
fi

CLIENT_ID="${CLIENT_ID:-${IB_CLIENT_ID:-1}}"
python3 "$ROOT/scripts/guard_ib_client_id.py" --client-id "$CLIENT_ID" --release 2>/dev/null || true

rm -f "$PID_FILE" "$SHUTDOWN_FILE"

if [ "$FORCED_KILL" = true ]; then
  echo "▶ Final Halim gold export + SFT + Colab zip (hard-kill fallback)…"
  python3 -c "
from core.config import BotConfig
from core.halim_gold_pipeline import run_halim_gold_pipeline
run_halim_gold_pipeline(
    BotConfig(),
    trigger='live_session_stop',
    prepare_sft=True,
    package_colab=True,
)
" 2>/dev/null || true

  if [ -d "$ROOT/venv" ]; then
    python3 -c "
from core.local_cleanup import cleanup_local_workspace
cleanup_local_workspace(aggressive=True)
" 2>/dev/null || true
  fi
else
  echo "✅ HANOON exited gracefully (in-process Halim + git flush already ran)"
fi

echo "✅ HANOON stopped"
echo "   Tip: use ./stop.sh — not Ctrl+C (may skip evolution on hard kill)"
