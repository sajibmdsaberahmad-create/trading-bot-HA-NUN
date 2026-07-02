#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# scripts/start_replay_live.sh — Full HANOON replay (identical ScalperRunner logic)
#
# Multi-ticker fake-live from intraday CSVs. Council + PPO + shadow fills.
# Does NOT touch live START.command or IB orders.
#
# Usage:
#   ./scripts/start_replay_live.sh              # chunked sessions (default 90 min, then auto-stop)
#   ./scripts/start_replay_live.sh chunk        # same — stop anytime or wait for session cap
#   ./scripts/start_replay_live.sh chunk 120    # 120-minute wall-clock session
#   ./scripts/start_replay_live.sh train        # no time cap (run until farm done or manual stop)
#   ./scripts/start_replay_live.sh realtime     # 1 timestamp step ≈ real elapsed gap
#   ./scripts/start_replay_live.sh turbo        # no pacing
#   ./scripts/start_replay_live.sh day          # one RTH day (auto-detected from CSVs)
#   ./scripts/stop_replay.sh                    # graceful stop — trims trained bars, resume next start
#
# Multi-session: IB download once (~60d). Stop/restart anytime — only walked bars are consumed.
# Re-download happens automatically only when the whole farm is trained.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_smart_sprint_env.sh" 2>/dev/null || true
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_memory_profile.sh" 2>/dev/null || true
# Replay gate profile — match live paper by default (quality gold); REPLAY_GOLD_VOLUME=true for legacy loose mode
export HANOON_DEVICE_PROFILE_ROOT="$ROOT"
if [[ "${REPLAY_GOLD_VOLUME:-false}" == "true" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/scripts/replay_gold_volume_profile.sh"
else
  export REPLAY_MATCH_LIVE="${REPLAY_MATCH_LIVE:-true}"
  # shellcheck disable=SC1091
  source "$ROOT/scripts/replay_match_live_profile.sh"
fi
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PACE="${1:-chunk}"
CHUNK_MIN="${2:-}"
REPLAY_ROOT="${REPLAY_DATA_DIR:-$ROOT/data/replay}"

export REPLAY_LIVE=true
export REPLAY_BLOCK_IB=true
export IB_TRUTH_STARTUP_CHECK="${IB_TRUTH_STARTUP_CHECK:-false}"
export IB_TRUTH_STARTUP_BLOCK="${IB_TRUTH_STARTUP_BLOCK:-false}"
export REPLAY_DATA_DIR="$REPLAY_ROOT"
export GIT_BATCH_CHECKPOINTS=true
export REPLAY_SLIPPAGE_MODEL="${REPLAY_SLIPPAGE_MODEL:-adaptive}"
export REPLAY_FILL_PROB="${REPLAY_FILL_PROB:-0.93}"
export REPLAY_PARTIAL_FILL_PROB="${REPLAY_PARTIAL_FILL_PROB:-0.14}"
# REPLAY_RELAX_* / MIN_PROFIT set by replay_match_live_profile or replay_gold_volume_profile
export REPLAY_MIN_PROFIT_PROB="${REPLAY_MIN_PROFIT_PROB:-${MIN_PROFIT_PROBABILITY:-0.58}}"
export PPO_TEACHER_ENABLED="${PPO_TEACHER_ENABLED:-true}"
export PPO_TEACHER_WIN_RATE_FLOOR="${PPO_TEACHER_WIN_RATE_FLOOR:-0.38}"
export PPO_TEACHER_EVERY_N_TRADES="${PPO_TEACHER_EVERY_N_TRADES:-4}"
export PPO_TEACHER_MIN_INTERVAL_SEC="${PPO_TEACHER_MIN_INTERVAL_SEC:-180}"
export TRADING_COPILOT_ENABLED="${TRADING_COPILOT_ENABLED:-true}"
export COPILOT_REFRESH_SEC="${COPILOT_REFRESH_SEC:-90}"
export OWNED_BRAIN_DEVICE="${OWNED_BRAIN_DEVICE:-m2_8gb}"
export OWNED_BRAIN_GIT_PUSH="${OWNED_BRAIN_GIT_PUSH:-true}"
export PPO_ENTRY_MICRO_STEPS="${PPO_ENTRY_MICRO_STEPS:-512}"
export GROQ_MODEL="${GROQ_MODEL:-llama-3.1-8b-instant}"
unset REPLAY_TICKER 2>/dev/null || true

case "$PACE" in
  chunk)
    export REPLAY_REALTIME_PACE=false
    export REPLAY_TIME_DILATION_MS="${REPLAY_TIME_DILATION_MS:-50}"
    if [[ -n "$CHUNK_MIN" ]]; then
      export REPLAY_SESSION_MAX_MINUTES="$CHUNK_MIN"
    fi
    export REPLAY_SESSION_MAX_MINUTES="${REPLAY_SESSION_MAX_MINUTES:-90}"
    ;;
  day)
    export REPLAY_REALTIME_PACE="${REPLAY_REALTIME_PACE:-false}"
    export REPLAY_TIME_DILATION_MS="${REPLAY_TIME_DILATION_MS:-50}"
    if [[ -z "${REPLAY_START:-}" ]] && [[ -d "$REPLAY_ROOT/intraday" ]]; then
      LATEST=$(ls -t "$REPLAY_ROOT/intraday"/*_1min.csv 2>/dev/null | head -1)
      if [[ -n "$LATEST" ]]; then
        DAY=$(tail -1 "$LATEST" | cut -d, -f1 | cut -dT -f1 | cut -d' ' -f1)
        export REPLAY_START="$DAY"
        export REPLAY_END="$DAY"
        echo "  One-day replay: REPLAY_START=$REPLAY_START REPLAY_END=$REPLAY_END"
      fi
    fi
    ;;
  realtime)
    export REPLAY_REALTIME_PACE=true
    export REPLAY_TIME_DILATION_MS=0
    ;;
  turbo)
    export REPLAY_REALTIME_PACE=false
    export REPLAY_TIME_DILATION_MS=0
    export REPLAY_SESSION_MAX_MINUTES=0
    ;;
  train)
    export REPLAY_REALTIME_PACE=false
    export REPLAY_TIME_DILATION_MS="${REPLAY_TIME_DILATION_MS:-50}"
    export REPLAY_SESSION_MAX_MINUTES=0
    ;;
  *)
    export REPLAY_REALTIME_PACE=false
    export REPLAY_TIME_DILATION_MS="${REPLAY_TIME_DILATION_MS:-50}"
    export REPLAY_SESSION_MAX_MINUTES="${REPLAY_SESSION_MAX_MINUTES:-90}"
    PACE="chunk"
    ;;
esac

export REPLAY_MODEL_PATH="${REPLAY_MODEL_PATH:-models/ppo_trader_replay.zip}"
if [[ ! -f "$REPLAY_MODEL_PATH" ]] && [[ -f "$ROOT/ppo_trader.zip" ]]; then
  mkdir -p models
  cp "$ROOT/ppo_trader.zip" "$REPLAY_MODEL_PATH"
  echo "  Seeded replay model from ppo_trader.zip"
fi

# ── Match live HANOON AI / council / PPO (market closed on wall clock is OK) ──
export COUNCIL_ENABLED="${COUNCIL_ENABLED:-true}"
export COUNCIL_BACKEND="${COUNCIL_BACKEND:-groq}"
export PPO_LEARN_EVERY_ENTRY="${PPO_LEARN_EVERY_ENTRY:-true}"
export PPO_ENTRY_MICRO_STEPS="${PPO_ENTRY_MICRO_STEPS:-512}"
export PPO_ENTRY_MICRO_ASYNC="${PPO_ENTRY_MICRO_ASYNC:-false}"
export PPO_ENTRY_MICRO_DEBOUNCE_SEC="${PPO_ENTRY_MICRO_DEBOUNCE_SEC:-0}"
export USE_ENHANCED_AI="${USE_ENHANCED_AI:-true}"
export SHADOW_ON_PAPER=true
export DYNAMIC_AI_NOTIFICATIONS=false
export DAILY_IB_LEARNING_ENABLED=false
export INCREMENTAL_TRAINING_ENABLED="${INCREMENTAL_TRAINING_ENABLED:-true}"
export REPLAY_TRAINING_ENABLED="${REPLAY_TRAINING_ENABLED:-true}"
export REPLAY_TRAIN_PROXY="${REPLAY_TRAIN_PROXY:-true}"
export REPLAY_PPO_INCREMENTAL_STEPS="${REPLAY_PPO_INCREMENTAL_STEPS:-2048}"
export HALIM_PPO_COEVOLUTION="${HALIM_PPO_COEVOLUTION:-true}"
export HALIM_PPO_DIALOGUE="${HALIM_PPO_DIALOGUE:-true}"
export HALIM_PPO_DIALOGUE_TELEGRAM="${HALIM_PPO_DIALOGUE_TELEGRAM:-false}"
export HALIM_PPO_GENERATIVE_REFLECT="${HALIM_PPO_GENERATIVE_REFLECT:-true}"
export HALIM_COMPANION_PING="${HALIM_COMPANION_PING:-true}"
export HALIM_COMPANION_LEARN="${HALIM_COMPANION_LEARN:-true}"
export HALIM_ACTION_LEARN="${HALIM_ACTION_LEARN:-true}"
export HALIM_REPLAY_GOLD_COLLECT="${HALIM_REPLAY_GOLD_COLLECT:-true}"
export HALIM_AUTO_PACKAGE_COLAB="${HALIM_AUTO_PACKAGE_COLAB:-true}"
export REPLAY_PREPARE_SFT="${REPLAY_PREPARE_SFT:-true}"
export HALIM_LEARN_PACKAGE_ON_STOP="${HALIM_LEARN_PACKAGE_ON_STOP:-true}"
# Entry gates: replay_match_live_profile (default) or replay_gold_volume_profile
export OFF_HOURS_HEAVY_TRAINING=false
export SCAN_RUN_DEFERRED_IB=false
export OFF_HOURS_SUSPEND_MARKET_DATA=false
export SCAN_DEFER_IB_ON_STARTUP=true
export USE_LIVE_IB_SCANNER=false
export TELEGRAM_LISTEN_ENABLED=false
export STARTUP_CURATED_WHEN_NOT_TRADABLE=false
export FAST_SCANNER_LOCK="${FAST_SCANNER_LOCK:-true}"
export AI_STREAM_WATCH_CAP="${AI_STREAM_WATCH_CAP:-10}"
export AI_STREAM_PRIORITY_COUNT="${AI_STREAM_PRIORITY_COUNT:-6}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"

export LEARNING_PERSISTENCE_ENABLED="${LEARNING_PERSISTENCE_ENABLED:-true}"
export LEARNING_SNAPSHOT_INTERVAL_SEC="${LEARNING_SNAPSHOT_INTERVAL_SEC:-900}"
export LEARNING_SNAPSHOT_SAVE_PPO="${LEARNING_SNAPSHOT_SAVE_PPO:-false}"
export LEARNING_SYNC_INTERVAL_SEC="${LEARNING_SYNC_INTERVAL_SEC:-1200}"
export LEARNING_LIVE_MICRO_PPO="${LEARNING_LIVE_MICRO_PPO:-false}"
export LEARNING_QUEUE_ONLY="${LEARNING_QUEUE_ONLY:-true}"
export LEARNING_HEAVY_EVERY_N_TRADES="${LEARNING_HEAVY_EVERY_N_TRADES:-16}"
export LEARNING_DEFER_FLUSH_EVERY_N_TRADES="${LEARNING_DEFER_FLUSH_EVERY_N_TRADES:-32}"
export PPO_REWARD_REPLAY_MAX_EPISODES="${PPO_REWARD_REPLAY_MAX_EPISODES:-24}"
export PERIODIC_CLEANUP_SEC="${PERIODIC_CLEANUP_SEC:-0}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"
export REPLAY_SKIP_CONSUMED="${REPLAY_SKIP_CONSUMED:-true}"
export REPLAY_TRIM_CONSUMED_ON_STOP="${REPLAY_TRIM_CONSUMED_ON_STOP:-true}"
export REPLAY_PURGE_ALL_ON_STOP="${REPLAY_PURGE_ALL_ON_STOP:-false}"
export REPLAY_PURGE_DATA_ON_STOP="${REPLAY_PURGE_DATA_ON_STOP:-true}"
export REPLAY_AUTO_DOWNLOAD="${REPLAY_AUTO_DOWNLOAD:-true}"

if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

python3 -c "from core.shutdown_control import clear_shutdown_request; clear_shutdown_request()" \
  2>/dev/null || rm -f "$ROOT/runtime/shutdown.request"

# Auto-download IB CSV farm when missing or all bars already trained
"$ROOT/scripts/replay_ensure_ib_farm.sh"

if [[ ! -d "$REPLAY_ROOT/intraday" ]] || [[ -z "$(find "$REPLAY_ROOT/intraday" -maxdepth 1 -name '*_1min.csv' -print -quit 2>/dev/null)" ]]; then
  echo "❌ No replay CSVs after auto-download — check IB Gateway and logs above."
  exit 1
fi

# IB farm depth check — warn if thin data limits training
PYTHONPATH=. python - <<'PY' 2>/dev/null || true
from core.replay_training import ib_farm_stats, log_ib_farm_banner
from core.config import BotConfig
log_ib_farm_banner(BotConfig())
st = ib_farm_stats()
if st.get("min_bars", 0) < 2000:
    print("  ⚠️  Thin IB farm — deepen: PYTHONPATH=. python scripts/download_ib_replay_data.py --days 60 --refresh-partial")
PY

TICKER_LIST=$(find "$REPLAY_ROOT/intraday" -maxdepth 1 -name '*_1min.csv' -print 2>/dev/null \
  | xargs -n1 basename 2>/dev/null | sed 's/_1min.csv//' | tr '\n' ',' | sed 's/,$//' || true)
if [[ -z "$TICKER_LIST" ]]; then
  echo "⚠️  No *_1min.csv tickers found under $REPLAY_ROOT/intraday"
  exit 1
fi

mkdir -p logs

"$ROOT/scripts/halim_stop.sh" --telegram-only 2>/dev/null || true

echo "🧠 Ensuring Halim serve is active (chat paused — replay has full focus)…"
"$ROOT/scripts/ensure_halim_active.sh" --serve-only --restart || echo "   Halim serve warning (non-fatal — see logs/halim_serve.log)"
if [ "${HALIM_STANDALONE_WATCHDOG:-true}" = "true" ]; then
  "$ROOT/scripts/start_halim_watchdog.sh" || echo "   Halim watchdog warning (see logs/halim_watchdog.log)"
fi

echo "══════════════════════════════════════════════════════════════"
echo "  REPLAY SCALPER — full HANOON clone (multi-ticker, council on)"
echo "  Universe: ${REPLAY_TICKERS:-$TICKER_LIST}"
echo "  Data: $REPLAY_ROOT"
echo "  Pace: $PACE (dilation=${REPLAY_TIME_DILATION_MS}ms realtime=$REPLAY_REALTIME_PACE)"
if [[ "${REPLAY_SESSION_MAX_MINUTES:-0}" != "0" ]]; then
  echo "  Session: ${REPLAY_SESSION_MAX_MINUTES} min wall clock — then auto-stop (resume next start)"
else
  echo "  Session: no time cap — stop with REPLAY_STOP.command when you want"
fi
echo "  Profile: ${REPLAY_MATCH_LIVE:-?} match-live | relax council=${REPLAY_RELAX_COUNCIL:-?} copilot=${REPLAY_RELAX_COPILOT:-?}"
echo "  Gates: profit_prob=${MIN_PROFIT_PROBABILITY:-?} commander_runtime=${COMMANDER_RUNTIME_ENABLED:-?} green=${GREEN_DOCTRINE_ENTRY:-?} capital=${CAPITAL_DISCIPLINE:-?}"
echo "  Council: $COUNCIL_ENABLED | Model: $REPLAY_MODEL_PATH"
  echo "  Training: queue-only=${LEARNING_QUEUE_ONLY:-true} teardown=${REPLAY_TRAINING_ENABLED} snapshot_ppo=${LEARNING_SNAPSHOT_SAVE_PPO:-false}"
  echo "  Halim: M. A. Halim (${HALIM_LM_BACKEND:-?}) coevolution=$HALIM_PPO_COEVOLUTION dialogue=$HALIM_PPO_DIALOGUE gold=$HALIM_ACTION_LEARN"
  if [[ "${HALIM_LOW_MEMORY_ACTIVE:-}" == "true" ]]; then
    echo "  Memory: low-RAM profile ON — dialogue deferred, async PPO, MLX backend"
  fi
echo "  Fills: stochastic ($REPLAY_SLIPPAGE_MODEL slip, partial=${REPLAY_PARTIAL_FILL_PROB})"
echo "  Data: auto-download ON | resume multi-session | trim trained bars on stop"
echo "  Git: deferred during replay → 1 sync at session end"
echo "  Stop: ./stop_replay.sh  (graceful — Halim gold + trim CSVs + evolution)
  Or double-click: REPLAY_STOP.command"
echo "══════════════════════════════════════════════════════════════"

# Preflight: refuse start if ledger thinks all bars are already trained
if ! PYTHONPATH=. python3 - <<'PY'
import sys
from core.replay_consumption import farm_has_unconsumed_data, farm_unconsumed_stats
st = farm_unconsumed_stats()
if not farm_has_unconsumed_data():
    print(f"❌ No fresh replay bars ({st.get('unconsumed_bars', 0):,} unconsumed).")
    print("   Re-run start — auto-download will fetch new IB data.")
    sys.exit(1)
print(
    f"✓ Resume ready: {st.get('unconsumed_bars', 0):,} fresh bars · "
    f"{st.get('tickers', 0)} tickers (stop anytime — picks up here next start)"
)
PY
then
  exit 1
fi

export HANOON_PID_FILE="${REPLAY_PID_FILE:-logs/replay.pid}"
PYTHONPATH=. "${ROOT}/venv/bin/python3" main.py --mode replay-live --ticker SPY --cash "${CASH:-1000}" \
  2>&1 | tee -a "logs/REPLAY_SCALPER.log"
