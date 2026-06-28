#!/usr/bin/env bash
# Graceful REPLAY shutdown — evolution + Halim gold + one git sync at end.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
PID_FILE="${REPLAY_PID_FILE:-$LOG_DIR/replay.pid}"
SHUTDOWN_FILE="${HANOON_SHUTDOWN_FILE:-$ROOT/runtime/shutdown.request}"
WAIT_SEC="${REPLAY_SHUTDOWN_WAIT_SEC:-180}"

export PYTHONPATH="${ROOT}/halim:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

echo "🛑 Graceful REPLAY shutdown (up to ${WAIT_SEC}s for evolution + git sync)…"

mkdir -p "$(dirname "$SHUTDOWN_FILE")"
if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

python3 -c "
from core.shutdown_control import request_shutdown
request_shutdown('stop_replay.sh')
print('   Shutdown request written')
" 2>/dev/null || touch "$SHUTDOWN_FILE"

python3 -c "
from core.config import BotConfig
from core.learning_persistence import fsync_critical_artifacts, snapshot_learning
fsync_critical_artifacts()
snapshot_learning(BotConfig(), trigger='pre_stop_replay', halim_export=True)
" 2>/dev/null || true

PIDS=()
if [ -f "$PID_FILE" ]; then
  RPID=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)
  if [ -n "$RPID" ] && kill -0 "$RPID" 2>/dev/null; then
    PIDS+=("$RPID")
  fi
fi
while IFS= read -r pid; do
  [ -n "$pid" ] || continue
  skip=0
  for existing in "${PIDS[@]:-}"; do
    [ "$existing" = "$pid" ] && skip=1 && break
  done
  [ "$skip" -eq 1 ] || PIDS+=("$pid")
done < <(pgrep -f "main.py --mode replay-live" 2>/dev/null || true)

if [ ${#PIDS[@]} -eq 0 ]; then
  echo "   No running replay session — running standalone data flush…"
  python3 -c "
from core.graceful_shutdown import run_standalone_shutdown_flush
run_standalone_shutdown_flush(replay=True)
" 2>/dev/null || true
  rm -f "$PID_FILE" "$SHUTDOWN_FILE"
  echo "✅ Replay data flush complete (no live process)"
  exit 0
fi

echo "   Sending SIGTERM to replay: ${PIDS[*]}"
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
  if [ $((elapsed % 15)) -eq 0 ]; then
    echo "   Waiting for replay teardown (evolution + git)… ${elapsed}s"
  fi
done

still_alive=()
for pid in "${PIDS[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    still_alive+=("$pid")
  fi
done

if [ ${#still_alive[@]} -gt 0 ]; then
  echo "⚠️  Replay still running after ${WAIT_SEC}s — SIGKILL ${still_alive[*]}"
  for pid in "${still_alive[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  sleep 1
  echo "   Running fallback data flush…"
  python3 -c "
from core.graceful_shutdown import run_standalone_shutdown_flush
run_standalone_shutdown_flush(replay=True)
" 2>/dev/null || true
fi

rm -f "$PID_FILE" "$SHUTDOWN_FILE"
echo "✅ Replay stopped — session data + Halim gold + git sync attempted"

if [[ "${REPLAY_PURGE_DATA_ON_STOP:-true}" == "true" ]]; then
  echo "✂️  Trimming already-trained replay bars (keeping fresh data)…"
  python3 -c "
from core.replay_consumption import finalize_replay_session, purge_all_on_stop
from core.replay_data_housekeeping import purge_replay_farm
fin = finalize_replay_session(hub=None, trigger='stop_replay', verbose=True)
if purge_all_on_stop() or (fin.get('steps', {}).get('unconsumed', {}).get('unconsumed_bars', 999) < 20):
    purge_replay_farm(verbose=True, force=True)
" 2>/dev/null || true
fi

echo "   Tip: use ./stop_replay.sh — not Ctrl+C (may skip evolution on hard kill)"
