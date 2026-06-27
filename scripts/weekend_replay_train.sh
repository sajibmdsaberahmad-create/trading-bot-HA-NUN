#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# scripts/weekend_replay_train.sh — Weekend long replay training (IB farm + loop)
#
# 1. IB HMDS download → data/replay/intraday only (client_id=1, then disconnect)
# 2. Replay runs offline from CSVs (no IB — avoids duplicate MD / 10197)
# 3. Auto-restart after each timeline complete until you stop the loop
#
# Usage:
#   ./scripts/weekend_replay_train.sh              # download 60d + loop train pace
#   ./scripts/weekend_replay_train.sh turbo
#   WEEKEND_SKIP_DOWNLOAD=true ./scripts/weekend_replay_train.sh
#
# Stop: ./scripts/stop_weekend_replay.sh  (or Ctrl+C in this terminal)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PACE="${1:-train}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
LOOP_PID_FILE="${WEEKEND_LOOP_PID_FILE:-$LOG_DIR/weekend_replay.pid}"
WEEKEND_LOG="${LOG_DIR}/WEEKEND_REPLAY.log"

# Single IB client — same as live HANOON (no client_id=99 ghost sessions)
export IB_PORT="${IB_PORT:-4002}"
export CLIENT_ID="${CLIENT_ID:-1}"
export IB_CLIENT_ID="${IB_CLIENT_ID:-$CLIENT_ID}"

IB_DAYS="${WEEKEND_IB_DAYS:-60}"
MAX_EPOCHS="${WEEKEND_MAX_EPOCHS:-0}"
PAUSE_SEC="${WEEKEND_PAUSE_SEC:-20}"
SKIP_DOWNLOAD="${WEEKEND_SKIP_DOWNLOAD:-false}"
GIT_PUSH="${WEEKEND_GIT_PUSH:-false}"

mkdir -p "$LOG_DIR" "$ROOT/runtime"
echo "$$" >"$LOOP_PID_FILE"

log() {
  local msg="[$(date '+%H:%M:%S')] $*"
  echo "$msg"
  echo "$msg" >>"$WEEKEND_LOG"
}

cleanup() {
  log "Weekend replay loop stopping…"
  rm -f "$LOOP_PID_FILE"
  unset WEEKEND_REPLAY_LOOP 2>/dev/null || true
  export -n WEEKEND_REPLAY_LOOP 2>/dev/null || true
  if pgrep -f "main.py --mode replay-live" >/dev/null 2>&1; then
    "$ROOT/scripts/stop_replay.sh" >>"$WEEKEND_LOG" 2>&1 || true
  fi
  if [[ "$GIT_PUSH" == "true" ]]; then
    log "Final git sync…"
    PYTHONPATH="${ROOT}/halim:${ROOT}" python3 -c "
from core.graceful_shutdown import flush_git_sync
flush_git_sync(replay=True)
" >>"$WEEKEND_LOG" 2>&1 || true
  fi
  log "▶ Purging replay CSV farm (training data — learning kept in models/)…"
  REPLAY_PURGE_DATA_ON_STOP=true WEEKEND_REPLAY_LOOP=0 PYTHONPATH="${ROOT}/halim:${ROOT}" \
    python3 -c "
from core.replay_data_housekeeping import purge_replay_farm
purge_replay_farm(verbose=True)
" 2>&1 | tee -a "$WEEKEND_LOG" || true
  log "Weekend replay loop ended."
}

trap cleanup EXIT INT TERM

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

export PYTHONPATH="${ROOT}/halim:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# One replay data root — intraday IB farm only (not hanoon daily yfinance)
export REPLAY_DATA_DIR="${REPLAY_DATA_DIR:-$ROOT/data/replay}"
export REPLAY_LIVE=true
export REPLAY_BLOCK_IB=true
unset REPLAY_START REPLAY_END REPLAY_TICKER 2>/dev/null || true
export OWNED_BRAIN_GIT_PUSH="$GIT_PUSH"
export GIT_BATCH_CHECKPOINTS=true

export LEARNING_PERSISTENCE_ENABLED="${LEARNING_PERSISTENCE_ENABLED:-true}"
export LEARNING_SNAPSHOT_INTERVAL_SEC="${LEARNING_SNAPSHOT_INTERVAL_SEC:-300}"
export LEARNING_SYNC_INTERVAL_SEC="${LEARNING_SYNC_INTERVAL_SEC:-600}"
export REPLAY_PURGE_DATA_ON_STOP="${REPLAY_PURGE_DATA_ON_STOP:-true}"
export WEEKEND_REPLAY_LOOP=1

python3 -c "from core.shutdown_control import clear_shutdown_request; clear_shutdown_request()" \
  2>/dev/null || rm -f "$ROOT/runtime/shutdown.request"

ensure_ib_free_for_download() {
  if pgrep -f "main.py.*--mode scalper" >/dev/null 2>&1; then
    log "❌ Live HANOON (scalper) is running on IB client_id=$CLIENT_ID"
    log "   Stop it first: ./stop.sh"
    exit 1
  fi
  if pgrep -f "main.py --mode replay-live" >/dev/null 2>&1; then
    log "   Stopping stale replay before IB download (client_id=$CLIENT_ID)…"
    "$ROOT/scripts/stop_replay.sh" >>"$WEEKEND_LOG" 2>&1 || true
    sleep 2
  fi
  if pgrep -f "main.py" >/dev/null 2>&1; then
    log "❌ Another main.py is still running — free client_id=$CLIENT_ID first"
    exit 1
  fi
}

log "══════════════════════════════════════════════════════════════"
log "  WEEKEND REPLAY TRAIN — IB farm → CSV replay loop"
log "  Pace: $PACE | IB days: $IB_DAYS | client_id: $CLIENT_ID | epochs: ${MAX_EPOCHS:-∞}"
log "  Data: $REPLAY_DATA_DIR/intraday (single source)"
log "  Stop: ./scripts/stop_weekend_replay.sh"
log "══════════════════════════════════════════════════════════════"

if [[ "$SKIP_DOWNLOAD" != "true" ]]; then
  ensure_ib_free_for_download
  log "▶ IB HMDS download (Gateway ${IB_HOST:-127.0.0.1}:$IB_PORT client_id=$CLIENT_ID)…"
  log "   (disconnects before replay — replay never opens IB)"
  if PYTHONPATH=. IB_PORT="$IB_PORT" CLIENT_ID="$CLIENT_ID" \
      python3 -u "$ROOT/scripts/download_ib_replay_data.py" \
      --days "$IB_DAYS" --client-id "$CLIENT_ID" --port "$IB_PORT" --merge \
      2>&1 | tee -a "$WEEKEND_LOG"; then
    log "  ✅ IB download pass complete — Gateway released"
  else
    log "  ⚠️  IB download failed or partial — continuing with existing CSVs"
  fi
  ensure_ib_free_for_download
  log "▶ Refresh thin tickers (<85% of fullest CSV)…"
  PYTHONPATH=. IB_PORT="$IB_PORT" CLIENT_ID="$CLIENT_ID" \
    python3 -u "$ROOT/scripts/download_ib_replay_data.py" \
    --days "$IB_DAYS" --client-id "$CLIENT_ID" --port "$IB_PORT" --refresh-partial \
    2>&1 | tee -a "$WEEKEND_LOG" || log "  ⚠️  refresh-partial skipped or nothing to refresh"
  sleep 2
  if pgrep -f "main.py" >/dev/null 2>&1; then
    log "❌ IB download left a main.py running — aborting"
    exit 1
  fi
else
  log "▶ Skipping IB download (WEEKEND_SKIP_DOWNLOAD=true)"
fi

farm_line="$(PYTHONPATH=. python3 - <<'PY' 2>/dev/null || true
from core.replay_training import ib_farm_stats
st = ib_farm_stats()
if st.get("ok"):
    print(
        f"Farm: {st['tickers']} tickers · {st['total_bars']:,} bars "
        f"(min={st.get('min_bars', 0):,} max={st.get('max_bars', 0):,})"
    )
else:
    print("⚠️  No intraday farm")
PY
)"
[[ -n "$farm_line" ]] && log "  $farm_line"

if [[ ! -d "$REPLAY_DATA_DIR/intraday" ]] || [[ -z "$(ls -A "$REPLAY_DATA_DIR/intraday" 2>/dev/null)" ]]; then
  log "❌ No $REPLAY_DATA_DIR/intraday CSVs — start IB Gateway and re-run"
  exit 1
fi

epoch=0
while true; do
  epoch=$((epoch + 1))
  if [[ "$MAX_EPOCHS" -gt 0 ]] && [[ "$epoch" -gt "$MAX_EPOCHS" ]]; then
    log "Reached WEEKEND_MAX_EPOCHS=$MAX_EPOCHS — done."
    break
  fi

  log ""
  log "══════════════════════════════════════════════════════════════"
  log "  EPOCH $epoch — replay ($PACE, full timeline, IB offline)"
  log "══════════════════════════════════════════════════════════════"

  if pgrep -f "main.py.*--mode scalper" >/dev/null 2>&1; then
    log "❌ Live HANOON started during weekend loop — stopping loop"
    break
  fi

  python3 -c "from core.shutdown_control import clear_shutdown_request; clear_shutdown_request()" \
    2>/dev/null || rm -f "$ROOT/runtime/shutdown.request"

  set +e
  "$ROOT/scripts/start_replay_live.sh" "$PACE"
  rc=$?
  set -e
  log "   Epoch $epoch exit code: $rc"

  if [[ -f "$ROOT/runtime/weekend_replay.stop" ]]; then
    log "Stop file detected — ending loop."
    rm -f "$ROOT/runtime/weekend_replay.stop"
    break
  fi

  if [[ "$rc" -ne 0 ]]; then
    log "⚠️  Epoch $epoch failed (code $rc)"
    if pgrep -f "main.py --mode replay-live" >/dev/null 2>&1; then
      "$ROOT/scripts/stop_replay.sh" >>"$WEEKEND_LOG" 2>&1 || true
    fi
    if [[ -f "$ROOT/runtime/shutdown.request" ]]; then
      log "   Shutdown requested — not starting next epoch."
      break
    fi
  else
    log "✅ Epoch $epoch complete (evolution at teardown)"
  fi

  log "   Next epoch in ${PAUSE_SEC}s…"
  sleep "$PAUSE_SEC"
done
