#!/usr/bin/env bash
# Stop the weekend replay loop + any active replay session.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
LOOP_PID_FILE="${WEEKEND_LOOP_PID_FILE:-$LOG_DIR/weekend_replay.pid}"
STOP_FILE="${ROOT}/runtime/weekend_replay.stop"

mkdir -p "$(dirname "$STOP_FILE")"
touch "$STOP_FILE"

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi
export PYTHONPATH="${ROOT}/halim:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "
from core.config import BotConfig
from core.learning_persistence import fsync_critical_artifacts, snapshot_learning
fsync_critical_artifacts()
snapshot_learning(BotConfig(), trigger='pre_stop_weekend', halim_export=True)
" 2>/dev/null || true

echo "🛑 Stopping weekend replay loop…"

# Stop replay FIRST — the loop bash ignores SIGTERM while blocked in start_replay_live.sh.
_replay_running=0
if pgrep -f "main.py --mode replay-live" >/dev/null 2>&1 \
  || pgrep -f "start_replay_live.sh" >/dev/null 2>&1; then
  _replay_running=1
elif [[ -f "${LOG_DIR}/replay.pid" ]]; then
  RPID=$(tr -d '[:space:]' <"${LOG_DIR}/replay.pid" 2>/dev/null || true)
  [[ -n "$RPID" ]] && kill -0 "$RPID" 2>/dev/null && _replay_running=1
fi

if [[ "$_replay_running" -eq 1 ]]; then
  echo "   Stopping active replay session…"
  WEEKEND_GIT_PUSH=false "$ROOT/scripts/stop_replay.sh"
else
  echo "   No active replay process"
fi

while IFS= read -r pid; do
  [[ -n "$pid" ]] || continue
  echo "   SIGTERM start_replay wrapper PID $pid"
  kill -TERM "$pid" 2>/dev/null || true
done < <(pgrep -f "start_replay_live.sh" 2>/dev/null || true)

if [[ -f "$LOOP_PID_FILE" ]]; then
  LPID=$(tr -d '[:space:]' <"$LOOP_PID_FILE" 2>/dev/null || true)
  if [[ -n "$LPID" ]] && kill -0 "$LPID" 2>/dev/null; then
    echo "   SIGTERM loop PID $LPID"
    kill -TERM "$LPID" 2>/dev/null || true
    for _ in $(seq 1 15); do
      kill -0 "$LPID" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$LPID" 2>/dev/null; then
      echo "   SIGKILL loop PID $LPID"
      kill -KILL "$LPID" 2>/dev/null || true
    fi
  fi
  rm -f "$LOOP_PID_FILE"
fi

if [[ "${WEEKEND_GIT_PUSH:-true}" == "true" ]]; then
  if [[ -d "$ROOT/venv" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/venv/bin/activate"
  fi
  export PYTHONPATH="${ROOT}/halim:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
  echo "   Git sync (replay artifacts)…"
  python3 -c "
from core.graceful_shutdown import flush_git_sync
flush_git_sync(replay=True)
" 2>/dev/null || true
fi

echo "🗑  Final replay cleanup (trim trained + purge leftovers)…"
export REPLAY_PURGE_DATA_ON_STOP=true
export WEEKEND_REPLAY_LOOP=0
export REPLAY_KEEP_CSV_BETWEEN_EPOCHS=false
export REPLAY_PURGE_ALL_ON_STOP=true
PYTHONPATH="${ROOT}/halim:${ROOT}" python3 -c "
from core.replay_consumption import finalize_replay_session
from core.replay_data_housekeeping import purge_replay_farm
finalize_replay_session(hub=None, trigger='stop_weekend', verbose=True)
purge_replay_farm(verbose=True, force=True)
" 2>/dev/null || true

rm -f "$STOP_FILE"

echo "✅ Weekend replay stopped"
