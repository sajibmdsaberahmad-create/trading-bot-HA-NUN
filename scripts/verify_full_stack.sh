#!/usr/bin/env bash
# Full stack verification — live HANOON then replay, one after another.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$ROOT/halim:$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export OWNED_BRAIN_GIT_PUSH="${OWNED_BRAIN_GIT_PUSH:-false}"
export GIT_BATCH_CHECKPOINTS=true

LOG="$ROOT/logs/verify_stack.log"
mkdir -p "$ROOT/logs" "$ROOT/runtime"
: > "$LOG"

PASS=0
FAIL=0
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }
check() {
  local name="$1"
  shift
  if "$@" >>"$LOG" 2>&1; then
    log "  ✅ $name"
    PASS=$((PASS + 1))
  else
    log "  ❌ $name"
    FAIL=$((FAIL + 1))
  fi
}

if [[ -d venv ]]; then source venv/bin/activate; fi

log "══════════════════════════════════════════════════════════════"
log "  HANOON + HALIM FULL STACK VERIFY (live → replay)"
log "══════════════════════════════════════════════════════════════"

# ── Pre-flight ─────────────────────────────────────────────────────────────
log ""
log "▶ Pre-flight"
check "replay CSV data" test -d "$ROOT/data/replay/intraday"
check "PPO model" test -f "$ROOT/models/ppo_trader_replay.zip" -o -f "$ROOT/ppo_trader.zip"
check "halim status" ./scripts/halim_status.sh
check "halim unlock" ./scripts/halim_chat.sh --unlock
check "halim chat" test -n "$(./scripts/halim_chat.sh 'stack verify ping' 2>/dev/null | head -1)"

# Stop anything leftover
./stop.sh >>"$LOG" 2>&1 || true
./stop_replay.sh >>"$LOG" 2>&1 || true
sleep 2

# ── LIVE HANOON ────────────────────────────────────────────────────────────
log ""
log "▶ LIVE HANOON (smoke — ${LIVE_SMOKE_SEC:-90}s then ./stop.sh)"
export IB_PORT="${IB_PORT:-4002}"
export CLIENT_ID="${CLIENT_ID:-1}"
export COUNCIL_ENABLED="${COUNCIL_ENABLED:-true}"
export HALIM_NATIVE="${HALIM_NATIVE:-false}"

python3 -u main.py --mode scalper --port "$IB_PORT" --client-id "$CLIENT_ID" \
  >>"$ROOT/logs/HANOON.log" 2>&1 &
LIVE_PID=$!
echo "$LIVE_PID" > "$ROOT/logs/hanoon.pid"
log "   Live PID $LIVE_PID — waiting for startup…"

LIVE_OK=0
for i in $(seq 1 45); do
  sleep 2
  if ! kill -0 "$LIVE_PID" 2>/dev/null; then
    log "   Live process exited early"
    tail -20 "$ROOT/logs/HANOON.log" | tee -a "$LOG"
    break
  fi
  if grep -q "Halim runtime\|HANOON SCALPER\|Startup lock complete\|Halim capabilities" "$ROOT/logs/HANOON.log" 2>/dev/null; then
    LIVE_OK=1
    log "   Live startup markers found (${i}x2s)"
    break
  fi
done

if [[ "$LIVE_OK" -eq 1 ]]; then
  PASS=$((PASS + 1))
  log "  ✅ live startup"
else
  FAIL=$((FAIL + 1))
  log "  ❌ live startup (check logs/HANOON.log)"
fi

sleep "${LIVE_SMOKE_SEC:-30}"
log "   Graceful live stop…"
./stop.sh >>"$LOG" 2>&1 || true
sleep 3
if pgrep -f "main.py --mode scalper" >/dev/null 2>&1; then
  FAIL=$((FAIL + 1))
  log "  ❌ live still running after stop.sh"
else
  PASS=$((PASS + 1))
  log "  ✅ live stopped cleanly"
fi

check "halim shutdown journal" test -f "$ROOT/models/halim_shutdown.jsonl"

# ── REPLAY ─────────────────────────────────────────────────────────────────
log ""
log "▶ REPLAY (turbo SOFI — ${REPLAY_SMOKE_SEC:-90}s then ./stop_replay.sh)"
export REPLAY_LIVE=true
export REPLAY_DATA_DIR="$ROOT/data/replay"
export REPLAY_TICKERS=SOFI
export REPLAY_REALTIME_PACE=false
export REPLAY_TIME_DILATION_MS=0
export REPLAY_MODEL_PATH="$ROOT/models/ppo_trader_replay.zip"
export HANOON_PID_FILE="$ROOT/logs/replay.pid"
export OWNED_BRAIN_GIT_PUSH=false
export DYNAMIC_AI_NOTIFICATIONS=false
export TELEGRAM_LISTEN_ENABLED=false

python3 -u main.py --mode replay-live --ticker SOFI --cash 1000 \
  >>"$ROOT/logs/REPLAY_SCALPER.log" 2>&1 &
REPLAY_PID=$!
echo "$REPLAY_PID" > "$ROOT/logs/replay.pid"
log "   Replay PID $REPLAY_PID — waiting for startup…"

REPLAY_OK=0
for i in $(seq 1 45); do
  sleep 2
  if ! kill -0 "$REPLAY_PID" 2>/dev/null; then
    log "   Replay process exited early"
    tail -30 "$ROOT/logs/REPLAY_SCALPER.log" | tee -a "$LOG"
    break
  fi
  if grep -q "REPLAY SCALPER\|Halim runtime\|REPLAY-LIVE SESSION COMPLETE" "$ROOT/logs/REPLAY_SCALPER.log" 2>/dev/null; then
    REPLAY_OK=1
    if grep -q "REPLAY-LIVE SESSION COMPLETE" "$ROOT/logs/REPLAY_SCALPER.log" 2>/dev/null; then
      log "   Replay finished timeline naturally"
      break
    fi
    if [[ "$REPLAY_OK" -eq 1 && "$i" -ge 3 ]]; then
      log "   Replay running (${i}x2s)"
      break
    fi
  fi
done

if [[ "$REPLAY_OK" -eq 1 ]]; then
  PASS=$((PASS + 1))
  log "  ✅ replay startup"
else
  FAIL=$((FAIL + 1))
  log "  ❌ replay startup (check logs/REPLAY_SCALPER.log)"
fi

if kill -0 "$REPLAY_PID" 2>/dev/null; then
  sleep "${REPLAY_SMOKE_SEC:-60}"
  log "   Graceful replay stop…"
  ./stop_replay.sh >>"$LOG" 2>&1 || true
  sleep 5
fi

if pgrep -f "main.py --mode replay-live" >/dev/null 2>&1; then
  FAIL=$((FAIL + 1))
  log "  ❌ replay still running after stop_replay.sh"
  ./stop_replay.sh >>"$LOG" 2>&1 || true
else
  PASS=$((PASS + 1))
  log "  ✅ replay stopped cleanly"
fi

# ── Post-session artifacts ─────────────────────────────────────────────────
log ""
log "▶ Post-session artifacts"
check "action log" test -f "$ROOT/halim/data/actions/action_log.jsonl"
check "coevolution log" test -f "$ROOT/halim/data/coevolution/correction_log.jsonl"
check "coevolution status" ./scripts/coevolution_status.sh >/dev/null
check "halim identity" test -f "$ROOT/models/halim_identity.json"
check "experience buffer" test -f "$ROOT/models/experience_buffer.jsonl"
grep -q "Halim git auto-push skipped during REPLAY\|Replay git\|co-evolution\|Graceful shutdown" \
  "$ROOT/logs/REPLAY_SCALPER.log" 2>/dev/null && { log "  ✅ replay log has expected markers"; PASS=$((PASS + 1)); } \
  || { log "  ❌ replay log missing expected markers"; FAIL=$((FAIL + 1)); }

log ""
log "══════════════════════════════════════════════════════════════"
log "  RESULTS: $PASS passed, $FAIL failed"
log "  Full log: $LOG"
log "══════════════════════════════════════════════════════════════"
[[ "$FAIL" -eq 0 ]]
