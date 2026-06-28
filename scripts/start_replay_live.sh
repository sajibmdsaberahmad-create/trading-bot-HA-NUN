#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# scripts/start_replay_live.sh — Full HANOON replay (identical ScalperRunner logic)
#
# Multi-ticker fake-live from intraday CSVs. Council + PPO + shadow fills.
# Does NOT touch live START.command or IB orders.
#
# Usage:
#   ./scripts/start_replay_live.sh              # all downloaded tickers, train pace
#   ./scripts/start_replay_live.sh train        # same
#   ./scripts/start_replay_live.sh realtime     # 1 timestamp step ≈ real elapsed gap
#   ./scripts/start_replay_live.sh turbo        # no pacing
#   REPLAY_START=2026-06-25 REPLAY_END=2026-06-26 ./scripts/start_replay_live.sh
#   ./scripts/start_replay_live.sh day   # one RTH day (auto-detected from CSVs)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_memory_profile.sh" 2>/dev/null || true
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PACE="${1:-train}"
REPLAY_ROOT="${REPLAY_DATA_DIR:-$ROOT/data/replay}"

export REPLAY_LIVE=true
export REPLAY_BLOCK_IB=true
export REPLAY_DATA_DIR="$REPLAY_ROOT"
# Git: all pushes deferred during replay → 1 consolidated sync at session end
export GIT_BATCH_CHECKPOINTS=true
# Stochastic fills — slippage, partials, latency (see core/replay_fill_simulator.py)
export REPLAY_SLIPPAGE_MODEL="${REPLAY_SLIPPAGE_MODEL:-adaptive}"
export REPLAY_FILL_PROB="${REPLAY_FILL_PROB:-0.93}"
export REPLAY_PARTIAL_FILL_PROB="${REPLAY_PARTIAL_FILL_PROB:-0.14}"
export REPLAY_RELAX_COUNCIL="${REPLAY_RELAX_COUNCIL:-true}"
export REPLAY_RELAX_COPILOT="${REPLAY_RELAX_COPILOT:-true}"
export REPLAY_MIN_PROFIT_PROB="${REPLAY_MIN_PROFIT_PROB:-0.45}"
# Teacher–student PPO (Groq/Gemini critiques PPO → distills into Halim + PPO)
export PPO_TEACHER_ENABLED="${PPO_TEACHER_ENABLED:-true}"
export PPO_TEACHER_WIN_RATE_FLOOR="${PPO_TEACHER_WIN_RATE_FLOOR:-0.38}"
export PPO_TEACHER_EVERY_N_TRADES="${PPO_TEACHER_EVERY_N_TRADES:-4}"
export PPO_TEACHER_MIN_INTERVAL_SEC="${PPO_TEACHER_MIN_INTERVAL_SEC:-180}"
export TRADING_COPILOT_ENABLED="${TRADING_COPILOT_ENABLED:-true}"
export COPILOT_REFRESH_SEC="${COPILOT_REFRESH_SEC:-90}"
# Owned brain — device-aware evolution at session end (M2 8GB default)
export OWNED_BRAIN_DEVICE="${OWNED_BRAIN_DEVICE:-m2_8gb}"
export OWNED_BRAIN_GIT_PUSH="${OWNED_BRAIN_GIT_PUSH:-true}"
export PPO_ENTRY_MICRO_STEPS="${PPO_ENTRY_MICRO_STEPS:-512}"
export GROQ_MODEL="${GROQ_MODEL:-llama-3.1-8b-instant}"
# All intraday tickers unless narrowed: REPLAY_TICKERS=SOFI,PLTR,MARA
unset REPLAY_TICKER 2>/dev/null || true

case "$PACE" in
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
    ;;
  train|*)
    export REPLAY_REALTIME_PACE=false
    export REPLAY_TIME_DILATION_MS="${REPLAY_TIME_DILATION_MS:-50}"
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
# Replay: looser entry gates for gold volume (live paper uses stricter defaults in start_hanoon.sh)
export REGIME_ENTRY_BLOCK="${REGIME_ENTRY_BLOCK:-false}"
export MTF_ENTRY_BLOCK="${MTF_ENTRY_BLOCK:-false}"
export USE_ACCOUNT_LOSS_HALT="${USE_ACCOUNT_LOSS_HALT:-false}"
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
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.65}"
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

if [[ ! -d "$REPLAY_ROOT/intraday" ]] || [[ -z "$(find "$REPLAY_ROOT/intraday" -maxdepth 1 -name '*_1min.csv' -print -quit 2>/dev/null)" ]]; then
  echo ""
  echo "⚠️  No intraday CSVs in $REPLAY_ROOT/intraday"
  echo "    Download replay data first (IB Gateway must be running):"
  echo "    cd \"$ROOT\" && PYTHONPATH=. python scripts/download_ib_replay_data.py --days 60"
  echo ""
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

if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

mkdir -p logs

"$ROOT/scripts/halim_stop.sh" --telegram-only 2>/dev/null || true

echo "🧠 Ensuring Halim serve is active (chat paused — replay has full focus)…"
"$ROOT/scripts/ensure_halim_active.sh" --serve-only || echo "   Halim serve warning (non-fatal — see logs/halim_serve.log)"

echo "══════════════════════════════════════════════════════════════"
echo "  REPLAY SCALPER — full HANOON clone (multi-ticker, council on)"
echo "  Universe: ${REPLAY_TICKERS:-$TICKER_LIST}"
echo "  Data: $REPLAY_ROOT"
echo "  Pace: $PACE (dilation=${REPLAY_TIME_DILATION_MS}ms realtime=$REPLAY_REALTIME_PACE)"
echo "  Council: $COUNCIL_ENABLED (relax=$REPLAY_RELAX_COUNCIL) | Model: $REPLAY_MODEL_PATH"
  echo "  Training: queue-only=${LEARNING_QUEUE_ONLY:-true} teardown=${REPLAY_TRAINING_ENABLED} snapshot_ppo=${LEARNING_SNAPSHOT_SAVE_PPO:-false}"
  echo "  Halim: M. A. Halim (${HALIM_LM_BACKEND:-?}) coevolution=$HALIM_PPO_COEVOLUTION dialogue=$HALIM_PPO_DIALOGUE gold=$HALIM_ACTION_LEARN"
  if [[ "${HALIM_LOW_MEMORY_ACTIVE:-}" == "true" ]]; then
    echo "  Memory: low-RAM profile ON — dialogue deferred, async PPO, MLX backend"
  fi
echo "  Fills: stochastic ($REPLAY_SLIPPAGE_MODEL slip, partial=${REPLAY_PARTIAL_FILL_PROB})"
echo "  Git: deferred during replay → 1 sync at session end"
echo "  Stop: ./stop_replay.sh  (graceful — Halim gold + evolution + git)
  Or double-click: REPLAY_STOP.command"
echo "══════════════════════════════════════════════════════════════"

export HANOON_PID_FILE="${REPLAY_PID_FILE:-logs/replay.pid}"
PYTHONPATH=. "${ROOT}/venv/bin/python3" main.py --mode replay-live --ticker SPY --cash "${CASH:-1000}" \
  2>&1 | tee -a "logs/REPLAY_SCALPER.log"
