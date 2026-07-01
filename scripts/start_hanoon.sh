#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# scripts/start_hanoon.sh — One command starts EVERYTHING for HANOON pilot mode
#
# Orchestrates:
#   1. venv + dependencies
#   2. Encrypted .env vault (cross-device secrets)
#   3. Git sync daemon (auto-push on any file change — any IDE)
#   4. Cloud council API keys (Groq + Gemini — no local Ollama)
#   5. Pre-flight checks (features, model, IB port)
#   6. HANOON scalper (live IB scanner, pilot mode, cognitive autopilot)
#
# Usage:
#   ./scripts/start_hanoon.sh
#   IB_PORT=4002 ./scripts/start_hanoon.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Homebrew / common paths (Ollama often not on minimal PATH)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# US market clock — device locale (e.g. Bangladesh) must not affect trading hours
export TZ="America/New_York"

IB_PORT="${IB_PORT:-4002}"
IB_HOST="${IB_HOST:-127.0.0.1}"

# HANOON uses a single fixed IB client ID (default 1). Do not auto-rotate —
# extra client IDs leave ghost sessions that block live market data (IB 10197).
CLIENT_ID="${CLIENT_ID:-1}"
export CLIENT_ID IB_CLIENT_ID="${IB_CLIENT_ID:-$CLIENT_ID}"
if [[ -x "$ROOT/scripts/guard_ib_client_id.py" ]]; then
  PY_GUARD="${PY:-python3}"
  if ! "$PY_GUARD" "$ROOT/scripts/guard_ib_client_id.py" --client-id "$CLIENT_ID" --port "$IB_PORT"; then
    echo "❌ IB client_id=${CLIENT_ID} guard failed — free the slot before starting HANOON."
    echo "   ./stop.sh   or   python3 scripts/guard_ib_client_id.py --client-id ${CLIENT_ID} --release"
    exit 1
  fi
elif pgrep -f "main.py.*--client-id[ =]${CLIENT_ID}([ ^]|$)" >/dev/null 2>&1; then
  echo "❌ Another process already uses IB client_id=${CLIENT_ID} — stop it first (./stop.sh)"
  exit 1
fi

# Free RAM before MLX + scalper (kills learn loop, IDE sidecars on ≤12GB Macs)
if [[ -x "$ROOT/scripts/sweep_device_junk.sh" && "${HALIM_DEVICE_SWEEP_ON_START:-false}" == "true" ]]; then
  "$ROOT/scripts/sweep_device_junk.sh" || true
elif [[ -x "$ROOT/scripts/free_ram_for_trading.sh" ]]; then
  "$ROOT/scripts/free_ram_for_trading.sh" || true
fi
# Cloud council — Groq primary, Gemini fallback (set keys in .env)
export COUNCIL_ENABLED="${COUNCIL_ENABLED:-true}"
export COUNCIL_BACKEND="${COUNCIL_BACKEND:-groq}"
export GROQ_MODEL="${GROQ_MODEL:-llama-3.3-70b-versatile}"
export GROQ_MODEL_FAST="${GROQ_MODEL_FAST:-llama-3.1-8b-instant}"
export GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
export GEMINI_VISION_MODEL="${GEMINI_VISION_MODEL:-gemini-2.5-flash}"
export COUNCIL_TIMEOUT_SEC="${COUNCIL_TIMEOUT_SEC:-12}"
export COUNCIL_MAX_TOKENS="${COUNCIL_MAX_TOKENS:-384}"
export COUNCIL_MIN_CALL_INTERVAL_SEC="${COUNCIL_MIN_CALL_INTERVAL_SEC:-0.5}"
export COUNCIL_BUDGET_ENABLED="${COUNCIL_BUDGET_ENABLED:-true}"
export COUNCIL_NOTIFY_API_ENABLED="${COUNCIL_NOTIFY_API_ENABLED:-false}"
export COUNCIL_NOTIFY_API_COPILOT="${COUNCIL_NOTIFY_API_COPILOT:-true}"
export COUNCIL_NOTIFY_API_TRADES="${COUNCIL_NOTIFY_API_TRADES:-false}"
export COUNCIL_DAILY_DIGEST_ENABLED="${COUNCIL_DAILY_DIGEST_ENABLED:-true}"
export COUNCIL_MOOD_API_ENABLED="${COUNCIL_MOOD_API_ENABLED:-false}"
export ENV_SYNC_ENABLED="${ENV_SYNC_ENABLED:-true}"
export TRADING_BOT_TELEGRAM_LISTEN="${TRADING_BOT_TELEGRAM_LISTEN:-true}"
export TRADING_BOT_TELEGRAM_VERIFY_SECRET="${TRADING_BOT_TELEGRAM_VERIFY_SECRET:-hall of fame}"
export AI_PAPER_FREE_LEARNING="${AI_PAPER_FREE_LEARNING:-true}"
export PAPER_EQUITY_HINT="${PAPER_EQUITY_HINT:-1000000}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
MAIN_LOG="$LOG_DIR/HANOON.log"
PID_FILE="$LOG_DIR/hanoon.pid"
export HANOON_LOG_PATH="$MAIN_LOG"

mkdir -p "$LOG_DIR" "$ROOT/models/daily_reports" "$ROOT/runtime"

# One canonical log: logs/HANOON.log (Python FileHandler — no tee duplicate)
if [[ -f "$ROOT/HANOON.log" && ! -L "$ROOT/HANOON.log" ]]; then
  cat "$ROOT/HANOON.log" >>"$MAIN_LOG" 2>/dev/null || true
  mv "$ROOT/HANOON.log" "$ROOT/HANOON.log.migrated.$(date +%s)" 2>/dev/null || true
fi
ln -sf "$MAIN_LOG" "$ROOT/HANOON.log" 2>/dev/null || true

export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"
export CAPITAL_DISCIPLINE="${CAPITAL_DISCIPLINE:-true}"
export TREAT_PAPER_AS_LIVE="${TREAT_PAPER_AS_LIVE:-true}"
export AI_SPIKE_FAST_ENTRY="${AI_SPIKE_FAST_ENTRY:-false}"
export PPO_LEAD_WHILE_COUNCIL_PENDING="${PPO_LEAD_WHILE_COUNCIL_PENDING:-true}"
export MIN_PROFIT_PROBABILITY="${MIN_PROFIT_PROBABILITY:-0.65}"
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.68}"
export ENTRY_QUALITY_BLEND_WEIGHT="${ENTRY_QUALITY_BLEND_WEIGHT:-0.55}"
export ENTRY_QUALITY_HARDNESS="${ENTRY_QUALITY_HARDNESS:-0.45}"
export CAPITAL_MIN_ENTRY_SCAN_SCORE="${CAPITAL_MIN_ENTRY_SCAN_SCORE:-55}"
export CAPITAL_MIN_ENTRY_SPIKE_RATIO="${CAPITAL_MIN_ENTRY_SPIKE_RATIO:-1.25}"
export CAPITAL_ENTRY_COOLDOWN_SEC="${CAPITAL_ENTRY_COOLDOWN_SEC:-0}"
export MAX_ENTRIES_PER_HOUR="${MAX_ENTRIES_PER_HOUR:-2}"
export AI_PROFIT_FULL_POWER="${AI_PROFIT_FULL_POWER:-true}"
export PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL="${PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL:-false}"
export GREEN_PROFIT_LOCK_ENABLED="${GREEN_PROFIT_LOCK_ENABLED:-true}"
export GREEN_PROFIT_LOCK_MIN_PNL_PCT="${GREEN_PROFIT_LOCK_MIN_PNL_PCT:-0.0025}"
export GREEN_PROFIT_LOCK_QUICK_SCALP_PCT="${GREEN_PROFIT_LOCK_QUICK_SCALP_PCT:-0.0035}"
export GREEN_PROFIT_LOCK_AI_WAIT_SEC="${GREEN_PROFIT_LOCK_AI_WAIT_SEC:-4.0}"
export GREEN_PROFIT_LOCK_GIVEBACK_PCT="${GREEN_PROFIT_LOCK_GIVEBACK_PCT:-0.22}"
export GREEN_PROFIT_LOCK_FADE_FLOOR_PCT="${GREEN_PROFIT_LOCK_FADE_FLOOR_PCT:-0.0015}"
export DAILY_IB_LEARNING_ENABLED="${DAILY_IB_LEARNING_ENABLED:-true}"
export DAILY_IB_PPO_TRAIN_STEPS="${DAILY_IB_PPO_TRAIN_STEPS:-15000}"
export DAILY_IB_LEARNING_ON_SESSION_END="${DAILY_IB_LEARNING_ON_SESSION_END:-true}"
export DAILY_IB_LEARNING_ON_MARKET_OPEN="${DAILY_IB_LEARNING_ON_MARKET_OPEN:-true}"
export GITHUB_CLEAN_ALGO_REPO="${GITHUB_CLEAN_ALGO_REPO:-sajibmdsaberahmad-create/HANOON}"
export HANOON_CLEAN_REPO_AUTO_PUBLISH="${HANOON_CLEAN_REPO_AUTO_PUBLISH:-true}"
export HANOON_CLEAN_PUBLISH_MIN_SEC="${HANOON_CLEAN_PUBLISH_MIN_SEC:-3600}"
export TRAILING_PROFIT_GIVEBACK_PCT="${TRAILING_PROFIT_GIVEBACK_PCT:-0.50}"
export AI_POSITION_MANAGE_IN_PROFIT_SEC="${AI_POSITION_MANAGE_IN_PROFIT_SEC:-1.0}"
export ALLOW_PRE_MARKET_TRADING="${ALLOW_PRE_MARKET_TRADING:-true}"
export ALLOW_AFTER_HOURS_TRADING="${ALLOW_AFTER_HOURS_TRADING:-false}"
export ALLOW_OVERNIGHT_TRADING="${ALLOW_OVERNIGHT_TRADING:-false}"
export FAST_SCANNER_LOCK="${FAST_SCANNER_LOCK:-true}"
export SCAN_MTF_DURING_RTH="${SCAN_MTF_DURING_RTH:-false}"
export SCAN_PREFETCH_LOCK_N="${SCAN_PREFETCH_LOCK_N:-8}"
export LOCK_BAR_WARM_BUDGET_SEC="${LOCK_BAR_WARM_BUDGET_SEC:-5}"
export DEFER_BAR_WARM_ON_LOCK="${DEFER_BAR_WARM_ON_LOCK:-true}"
export DEFER_FEATURE_VALIDATION="${DEFER_FEATURE_VALIDATION:-true}"
export AI_FULL_CAPITAL_ACCESS="${AI_FULL_CAPITAL_ACCESS:-false}"
export AI_ACCOUNT_EVAL_ON_STARTUP="${AI_ACCOUNT_EVAL_ON_STARTUP:-false}"
export FAST_MONITOR_SEC="${FAST_MONITOR_SEC:-0.10}"
export FLAT_LOOP_LOCKED_SEC="${FLAT_LOOP_LOCKED_SEC:-0.1}"
export POSITION_LOOP_IN_PROFIT_SEC="${POSITION_LOOP_IN_PROFIT_SEC:-0.05}"
export TICK_SPIKE_MONITOR="${TICK_SPIKE_MONITOR:-true}"
export AI_COUNCIL_MAX_WAIT_SEC="${AI_COUNCIL_MAX_WAIT_SEC:-4}"
export ENTRY_FILL_WAIT_SEC="${ENTRY_FILL_WAIT_SEC:-0.25}"
export BACKGROUND_WATCH_SEC="${BACKGROUND_WATCH_SEC:-15}"
export SCALPER_MICRO_PREDICT_ENABLED="${SCALPER_MICRO_PREDICT_ENABLED:-true}"
export SCALPER_LIVE_BARS_FIRST="${SCALPER_LIVE_BARS_FIRST:-true}"
export FAST_LOCK_SKIP_HISTORICAL="${FAST_LOCK_SKIP_HISTORICAL:-true}"
export PROFIT_HUNT_MAJOR_EXCHANGES_ONLY="${PROFIT_HUNT_MAJOR_EXCHANGES_ONLY:-true}"
export PROFIT_LOCK_ULTRA_FAST="${PROFIT_LOCK_ULTRA_FAST:-true}"
export DEFERRED_COUNCIL_LEARNING="${DEFERRED_COUNCIL_LEARNING:-true}"
export PPO_LEARNING_WEIGHT="${PPO_LEARNING_WEIGHT:-1.5}"
export PPO_LEARN_EVERY_ENTRY="${PPO_LEARN_EVERY_ENTRY:-true}"
export PPO_ENTRY_MICRO_STEPS="${PPO_ENTRY_MICRO_STEPS:-512}"
export PPO_ENTRY_MICRO_ASYNC="${PPO_ENTRY_MICRO_ASYNC:-false}"
export PPO_ENTRY_MICRO_DEBOUNCE_SEC="${PPO_ENTRY_MICRO_DEBOUNCE_SEC:-0}"
export AI_STREAM_WATCH_CAP="${AI_STREAM_WATCH_CAP:-10}"
export AI_STREAM_PRIORITY_COUNT="${AI_STREAM_PRIORITY_COUNT:-6}"
export SCALP_PROFIT_GIVEBACK_PCT="${SCALP_PROFIT_GIVEBACK_PCT:-0.20}"
export IN_PROFIT_MANAGE_PNL_PCT="${IN_PROFIT_MANAGE_PNL_PCT:-0.002}"
export LOCK_BAR_REFRESH_SEC="${LOCK_BAR_REFRESH_SEC:-90}"
export LOCK_STALE_RELEASE_SEC="${LOCK_STALE_RELEASE_SEC:-900}"
export LOCK_FOCUS_ROTATE_SEC="${LOCK_FOCUS_ROTATE_SEC:-0}"
export AI_FAST_EXECUTION="${AI_FAST_EXECUTION:-true}"
export AI_TICK_STREAM_COUNT="${AI_TICK_STREAM_COUNT:-4}"
export IB_MAX_REALTIME_BAR_STREAMS="${IB_MAX_REALTIME_BAR_STREAMS:-4}"
export USE_TICK_STREAM="${USE_TICK_STREAM:-true}"
export TICK_BY_TICK_TYPE="${TICK_BY_TICK_TYPE:-AllLast}"
export PAPER_REALTIME_BARS_ONLY="${PAPER_REALTIME_BARS_ONLY:-true}"
export PAPER_USE_HISTORICAL_BARS="${PAPER_USE_HISTORICAL_BARS:-true}"
export PAPER_REALTIME_BARS_USE_RTH="${PAPER_REALTIME_BARS_USE_RTH:-false}"
export IB_FORCE_LIVE_MARKET_DATA="${IB_FORCE_LIVE_MARKET_DATA:-true}"
export IB_MARKET_DATA_TYPE="${IB_MARKET_DATA_TYPE:-1}"
export IB_RECLAIM_SESSION_ON_START="${IB_RECLAIM_SESSION_ON_START:-true}"
export IB_SESSION_RECLAIM_PAUSE_SEC="${IB_SESSION_RECLAIM_PAUSE_SEC:-8}"
export IB_10197_RECLAIM_THRESHOLD="${IB_10197_RECLAIM_THRESHOLD:-3}"
export IB_10197_RECLAIM_COOLDOWN_SEC="${IB_10197_RECLAIM_COOLDOWN_SEC:-90}"
export IB_10197_STORM_THRESHOLD="${IB_10197_STORM_THRESHOLD:-3}"
export IB_10197_STORM_BACKOFF_SEC="${IB_10197_STORM_BACKOFF_SEC:-300}"
export IB_SCANNER_WARMUP_SEC="${IB_SCANNER_WARMUP_SEC:-5}"
export IB_SCANNER_OUTSIDE_RTH="${IB_SCANNER_OUTSIDE_RTH:-true}"
export IB_SCANNER_EXTENDED_FILTERS="${IB_SCANNER_EXTENDED_FILTERS:-true}"
export IB_SCANNER_EXTENDED_HOURS="${IB_SCANNER_EXTENDED_HOURS:-true}"
export IB_SCANNER_EXTENDED_PER_CODE_SEC="${IB_SCANNER_EXTENDED_PER_CODE_SEC:-12}"
export IB_SCANNER_EMPTY_BAIL_SEC="${IB_SCANNER_EMPTY_BAIL_SEC:-6}"
export STARTUP_CURATED_WHEN_NOT_TRADABLE="${STARTUP_CURATED_WHEN_NOT_TRADABLE:-true}"
export SCAN_DEFER_IB_ON_STARTUP="${SCAN_DEFER_IB_ON_STARTUP:-false}"
export OFF_HOURS_SUSPEND_MARKET_DATA="${OFF_HOURS_SUSPEND_MARKET_DATA:-true}"
export STARTUP_LOG_COMPACT="${STARTUP_LOG_COMPACT:-true}"
export SCAN_RUN_DEFERRED_IB="${SCAN_RUN_DEFERRED_IB:-true}"
export SKIP_HMDS_OUTSIDE_RTH="${SKIP_HMDS_OUTSIDE_RTH:-true}"
export MD_SOFT_FAIL_OUTSIDE_RTH="${MD_SOFT_FAIL_OUTSIDE_RTH:-true}"
export MD_SOFT_FAIL_HMDS="${MD_SOFT_FAIL_HMDS:-true}"
export RTH_OPENING_WINDOW_MIN="${RTH_OPENING_WINDOW_MIN:-30}"
export RTH_OPENING_MONITOR_SEC="${RTH_OPENING_MONITOR_SEC:-0.05}"
export RTH_MONITOR_SEC="${RTH_MONITOR_SEC:-0.08}"
export RTH_OPEN_FORCE_RESCAN="${RTH_OPEN_FORCE_RESCAN:-true}"
export RTH_OPEN_STREAM_REFRESH="${RTH_OPEN_STREAM_REFRESH:-true}"
export REALTIME_BARS_USE_RTH_WHEN_OPEN="${REALTIME_BARS_USE_RTH_WHEN_OPEN:-true}"
export SHADOW_ON_PAPER="${SHADOW_ON_PAPER:-false}"
export SHADOW_RESUME_ON_START="${SHADOW_RESUME_ON_START:-true}"
export AI_PRIORITY_TICK_STREAMS="${AI_PRIORITY_TICK_STREAMS:-false}"
export USE_FIXED_DEPLOY_CAP="${USE_FIXED_DEPLOY_CAP:-false}"
export USE_FIXED_RISK_CAP="${USE_FIXED_RISK_CAP:-false}"
export AI_DEFINE_ALL_LIMITS="${AI_DEFINE_ALL_LIMITS:-true}"
export AI_SESSION_LIMITS_OLLAMA="${AI_SESSION_LIMITS_OLLAMA:-true}"
export USE_ACCOUNT_LOSS_HALT="${USE_ACCOUNT_LOSS_HALT:-true}"
export REGIME_ENTRY_BLOCK="${REGIME_ENTRY_BLOCK:-true}"
export MTF_ENTRY_BLOCK="${MTF_ENTRY_BLOCK:-true}"
export PPO_REWARD_FEE_AWARE="${PPO_REWARD_FEE_AWARE:-true}"
export PPO_PROMOTION_GATE="${PPO_PROMOTION_GATE:-true}"
export USE_MULTI_POSITION="${USE_MULTI_POSITION:-true}"
export AI_UNLIMITED_MODE="${AI_UNLIMITED_MODE:-true}"
export AI_COUNCIL_ALL_DECISIONS="${AI_COUNCIL_ALL_DECISIONS:-true}"
export AI_SCAN_UNIVERSE_MAX="${AI_SCAN_UNIVERSE_MAX:-50}"
export PARALLEL_ENTRY_EXIT="${PARALLEL_ENTRY_EXIT:-true}"
export HOT_SWAP_ON_EXIT="${HOT_SWAP_ON_EXIT:-true}"
export FOCUS_PIN_TOP_PICK="${FOCUS_PIN_TOP_PICK:-false}"
export HYBRID_DISTILL_AUTO_FAST_PATH="${HYBRID_DISTILL_AUTO_FAST_PATH:-true}"
export HYBRID_DISTILL_MIN_TRADES="${HYBRID_DISTILL_MIN_TRADES:-10}"
# Halim toddler LM + PPO↔Halim distillation always on
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh" 2>/dev/null || true
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_memory_profile.sh" 2>/dev/null || true
export LEARNING_QUEUE_ONLY="${LEARNING_QUEUE_ONLY:-false}"
export LEARNING_LIVE_MICRO_PPO="${LEARNING_LIVE_MICRO_PPO:-false}"
export LEARNING_DEFER_DURING_RTH="${LEARNING_DEFER_DURING_RTH:-true}"
export LEARNING_SNAPSHOT_SAVE_PPO="${LEARNING_SNAPSHOT_SAVE_PPO:-false}"
export LEARNING_SNAPSHOT_INTERVAL_SEC="${LEARNING_SNAPSHOT_INTERVAL_SEC:-900}"
export LEARNING_PUSH_ON_TRADE="${LEARNING_PUSH_ON_TRADE:-false}"
export GIT_PUSH_DURING_SESSION="${GIT_PUSH_DURING_SESSION:-false}"
export START_GIT_SYNC_WITH_HANOON="${START_GIT_SYNC_WITH_HANOON:-false}"
export REQUIRE_IB_FILL_SYNC="${REQUIRE_IB_FILL_SYNC:-true}"
export IB_FILL_STRICT="${IB_FILL_STRICT:-true}"
export IB_FILL_FORCE_SEC="${IB_FILL_FORCE_SEC:-120}"
export SMART_STACK="${SMART_STACK:-true}"
export RAM_LIVE_ONLY="${RAM_LIVE_ONLY:-true}"
export LEARNING_LIVE_WEIGHT_EVERY_N_TRADES="${LEARNING_LIVE_WEIGHT_EVERY_N_TRADES:-0}"
export PPO_BYPASS_REQUIRES_BUY="${PPO_BYPASS_REQUIRES_BUY:-true}"
export PPO_OVERRIDE_ENTRY_REWARD="${PPO_OVERRIDE_ENTRY_REWARD:--0.15}"
export SPIKE_FAST_REQUIRES_QUALITY="${SPIKE_FAST_REQUIRES_QUALITY:-true}"
export TICKER_LOSS_COOLDOWN_SEC="${TICKER_LOSS_COOLDOWN_SEC:-180}"
export TICKER_LOSS_COOLDOWN_REPEAT_SEC="${TICKER_LOSS_COOLDOWN_REPEAT_SEC:-600}"
export TICKER_LOSS_COOLDOWN_TIER3_SEC="${TICKER_LOSS_COOLDOWN_TIER3_SEC:-1200}"
export TICKER_LOSS_COOLDOWN_TIER4_SEC="${TICKER_LOSS_COOLDOWN_TIER4_SEC:-1800}"
export GUARD_REPEAT_LOSS_CONF_BUMP="${GUARD_REPEAT_LOSS_CONF_BUMP:-0.04}"
export GUARD_REPEAT_LOSS_CONF_CAP="${GUARD_REPEAT_LOSS_CONF_CAP:-0.16}"
export GUARD_SESSION_BAN_AFTER="${GUARD_SESSION_BAN_AFTER:-5}"
export HALIM_ENTRY_REPEAT_LOSER_VETO="${HALIM_ENTRY_REPEAT_LOSER_VETO:-0.72}"
export COPILOT_CAUTION_CONF_BUMP="${COPILOT_CAUTION_CONF_BUMP:-0.08}"
# War account — paper trains with higher budget + more trips; live stays $1k / 2 trips
export WAR_ACCOUNT_ENABLED="${WAR_ACCOUNT_ENABLED:-true}"
export WAR_CAPITAL_USD="${WAR_CAPITAL_USD:-1000}"
export IB_TRUTH_RTH_SESSION="${IB_TRUTH_RTH_SESSION:-false}"
export IB_TRUTH_RTH_FILLS_ONLY="${IB_TRUTH_RTH_FILLS_ONLY:-false}"
export MACRO_FROM_IB="${MACRO_FROM_IB:-true}"
export IB_MACRO_TTL_SEC="${IB_MACRO_TTL_SEC:-120}"
export WAR_IB_SYNC="${WAR_IB_SYNC:-true}"
export WAR_IB_SYNC_INTERVAL_SEC="${WAR_IB_SYNC_INTERVAL_SEC:-90}"
export SWING_SHADOW_ENABLED="${SWING_SHADOW_ENABLED:-true}"
export SWING_SHADOW_INTERVAL_SEC="${SWING_SHADOW_INTERVAL_SEC:-900}"
export SWING_PAPER_ENABLED="${SWING_PAPER_ENABLED:-false}"
export SWING_IB_LIVE="${SWING_IB_LIVE:-true}"
export CAPITAL_PHASES_ENABLED="${CAPITAL_PHASES_ENABLED:-true}"
export CAPITAL_PHASE_SKIP_LAB="${CAPITAL_PHASE_SKIP_LAB:-true}"
# Unified green entry/exit — same war tactics on full-balance pre/post war; only sizing differs
export GREEN_DOCTRINE_UNIFIED="${GREEN_DOCTRINE_UNIFIED:-true}"
export GREEN_DOCTRINE_ENTRY="${GREEN_DOCTRINE_ENTRY:-true}"
export GREEN_DOCTRINE_EXIT="${GREEN_DOCTRINE_EXIT:-true}"
export GREEN_MULTIBAR_RIDE="${GREEN_MULTIBAR_RIDE:-true}"
export GREEN_SLIPPAGE_EXIT="${GREEN_SLIPPAGE_EXIT:-true}"
export GREEN_MULTIBAR_MAX_BARS="${GREEN_MULTIBAR_MAX_BARS:-5}"
export SWING_DOCTRINE_ENABLED="${SWING_DOCTRINE_ENABLED:-true}"
export SWING_MULTIBAR_MAX_DAYS="${SWING_MULTIBAR_MAX_DAYS:-12}"
export SWING_DOCTRINE_TRIP_MATURE="${SWING_DOCTRINE_TRIP_MATURE:-24}"
export SWING_IB_SCAN_INTERVAL_SEC="${SWING_IB_SCAN_INTERVAL_SEC:-600}"
export SWING_IB_MAX_POSITIONS="${SWING_IB_MAX_POSITIONS:-3}"
export SWING_INTEL_ENABLED="${SWING_INTEL_ENABLED:-true}"
export SWING_WEB_LEARN="${SWING_WEB_LEARN:-true}"
export SWING_WEB_LEARN_BATCH="${SWING_WEB_LEARN_BATCH:-4}"
export WAR_SWING_PAPER_USD="${WAR_SWING_PAPER_USD:-2000}"
export POSITION_HORIZON_ENABLED="${POSITION_HORIZON_ENABLED:-false}"
export IB_EXTENDED_ENABLED="${IB_EXTENDED_ENABLED:-true}"
export IB_HUB_ENABLED="${IB_HUB_ENABLED:-true}"
export IB_TRUTH_STARTUP_CHECK="${IB_TRUTH_STARTUP_CHECK:-true}"
export IB_TRUTH_STARTUP_BLOCK="${IB_TRUTH_STARTUP_BLOCK:-true}"
export IB_TRUTH_STARTUP_WAIT_SEC="${IB_TRUTH_STARTUP_WAIT_SEC:-20}"
export IB_TRUTH_STARTUP_MAX_AGE_SEC="${IB_TRUTH_STARTUP_MAX_AGE_SEC:-30}"
export IB_TRUTH_RUNTIME_MAX_AGE_SEC="${IB_TRUTH_RUNTIME_MAX_AGE_SEC:-90}"
export IB_EXTENDED_FULL_TTL_SEC="${IB_EXTENDED_FULL_TTL_SEC:-3600}"
export IB_EXTENDED_LIGHT_TTL_SEC="${IB_EXTENDED_LIGHT_TTL_SEC:-90}"
export IB_WHATIF_MARGIN_GATE="${IB_WHATIF_MARGIN_GATE:-true}"
export IB_FUNDAMENTAL_REPORT="${IB_FUNDAMENTAL_REPORT:-ReportSnapshot}"
export WAR_LIVE_OPERATING_CAPITAL="${WAR_LIVE_OPERATING_CAPITAL:-0}"
export WAR_BULLETS="${WAR_BULLETS:-8}"
export WAR_MAX_ROUND_TRIPS_PER_DAY="${WAR_MAX_ROUND_TRIPS_PER_DAY:-2}"
export WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY="${WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY:-8}"
export WAR_PAPER_LAB_MAX_ROUND_TRIPS_PER_DAY="${WAR_PAPER_LAB_MAX_ROUND_TRIPS_PER_DAY:-6}"
export WAR_FRESH_TRIPS_ON_START="${WAR_FRESH_TRIPS_ON_START:-true}"
export WAR_BALANCE_DRIVEN_TRIPS="${WAR_BALANCE_DRIVEN_TRIPS:-true}"
export WAR_BALANCE_DRIVEN_LAB="${WAR_BALANCE_DRIVEN_LAB:-true}"
export WAR_AI_SIZING="${WAR_AI_SIZING:-true}"
export WAR_CASH_RESERVE_PCT="${WAR_CASH_RESERVE_PCT:-0.05}"
export WAR_AUTO_RESET_AT_RTH="${WAR_AUTO_RESET_AT_RTH:-true}"
export WAR_MAX_ENTRIES_PER_HOUR="${WAR_MAX_ENTRIES_PER_HOUR:-2}"
export WAR_PAPER_MAX_ENTRIES_PER_HOUR="${WAR_PAPER_MAX_ENTRIES_PER_HOUR:-5}"
export MAX_ENTRIES_PER_HOUR="${MAX_ENTRIES_PER_HOUR:-5}"
export WAR_PAPER_SETTLEMENT_DAYS="${WAR_PAPER_SETTLEMENT_DAYS:-0}"
export WAR_SETTLEMENT_DAYS="${WAR_SETTLEMENT_DAYS:-1}"
export WAR_LAB_ENABLED="${WAR_LAB_ENABLED:-true}"
export WAR_LAB_CAPITAL_USD="${WAR_LAB_CAPITAL_USD:-5000}"
export WAR_LAB_MAX_ROUND_TRIPS_PER_DAY="${WAR_LAB_MAX_ROUND_TRIPS_PER_DAY:-2}"
export WAR_COMMISSION_PER_SIDE_USD="${WAR_COMMISSION_PER_SIDE_USD:-0.35}"
export WAR_SNIPER_MODE="${WAR_SNIPER_MODE:-true}"
export WAR_SNIPER_CONF_BUMP="${WAR_SNIPER_CONF_BUMP:-0.03}"
export HALIM_TELEGRAM_TRADE_NOTIFY="${HALIM_TELEGRAM_TRADE_NOTIFY:-true}"
# Sniper flash execution — catch vol/green before it fades (PPO-led, no council wait)
export SNIPER_STRONG_SPIKE_SCORE="${SNIPER_STRONG_SPIKE_SCORE:-45}"
export SNIPER_STRONG_SPIKE_RATIO="${SNIPER_STRONG_SPIKE_RATIO:-1.18}"
export SNIPER_FLASH_SPIKE_RATIO="${SNIPER_FLASH_SPIKE_RATIO:-1.22}"
export SNIPER_FLASH_MIN_SCORE="${SNIPER_FLASH_MIN_SCORE:-35}"
export SNIPER_FLASH_MIN_PPO_CONF="${SNIPER_FLASH_MIN_PPO_CONF:-0.50}"
export SNIPER_CONF_BUMP_ON_FLASH="${SNIPER_CONF_BUMP_ON_FLASH:-0}"
export SNIPER_CONF_BUMP_ON_STRONG="${SNIPER_CONF_BUMP_ON_STRONG:-0.02}"
export SNIPER_MIN_ENTRY_SCAN_SCORE="${SNIPER_MIN_ENTRY_SCAN_SCORE:-38}"
export SNIPER_MIN_ENTRY_SPIKE_RATIO="${SNIPER_MIN_ENTRY_SPIKE_RATIO:-1.15}"
export SNIPER_COUNCIL_MAX_WAIT_SEC="${SNIPER_COUNCIL_MAX_WAIT_SEC:-1.5}"
export SNIPER_SKIP_COUNCIL_ON_PPO_HOLD="${SNIPER_SKIP_COUNCIL_ON_PPO_HOLD:-true}"
export SNIPER_PPO_HOLD_SKIP_SEC="${SNIPER_PPO_HOLD_SKIP_SEC:-2.0}"
export SNIPER_MAX_CONFIDENCE_THRESHOLD="${SNIPER_MAX_CONFIDENCE_THRESHOLD:-0.65}"
export MTF_BAR_CACHE_SEC="${MTF_BAR_CACHE_SEC:-60}"
# Bar warm — priority tickers reach PPO sensors faster in sniper mode
export AI_MIN_BARS_FOCUS="${AI_MIN_BARS_FOCUS:-4}"
export SNIPER_MIN_BARS_FOCUS="${SNIPER_MIN_BARS_FOCUS:-4}"
export BAR_WARM_PER_LOOP="${BAR_WARM_PER_LOOP:-6}"
export SNIPER_FORCE_BAR_PREFETCH="${SNIPER_FORCE_BAR_PREFETCH:-true}"
export SNIPER_STRONG_MIN_PPO_CONF="${SNIPER_STRONG_MIN_PPO_CONF:-0.50}"
export SNIPER_COLD_VOL_MIN_SPIKE="${SNIPER_COLD_VOL_MIN_SPIKE:-2.0}"
export SNIPER_COLD_VOL_MIN_SCORE="${SNIPER_COLD_VOL_MIN_SCORE:-70}"
# War entry doctrine — block timeout junk, lottery band, risk-off flash only
export WAR_BLOCK_SCANNER_TIMEOUT="${WAR_BLOCK_SCANNER_TIMEOUT:-true}"
export WAR_BLOCK_SCANNER_FAST="${WAR_BLOCK_SCANNER_FAST:-true}"
export WAR_MIN_ENTRY_CONFIDENCE="${WAR_MIN_ENTRY_CONFIDENCE:-0.65}"
export WAR_MIN_PROFIT_PROBABILITY="${WAR_MIN_PROFIT_PROBABILITY:-0.80}"
export WAR_PAPER_MIN_ENTRY_CONFIDENCE="${WAR_PAPER_MIN_ENTRY_CONFIDENCE:-0.58}"
export WAR_PAPER_MIN_PROFIT_PROBABILITY="${WAR_PAPER_MIN_PROFIT_PROBABILITY:-0.62}"
export WAR_PAPER_MACRO_STAND_ASIDE="${WAR_PAPER_MACRO_STAND_ASIDE:-false}"
export WAR_BLOCK_CONFIDENCE_RAISE="${WAR_BLOCK_CONFIDENCE_RAISE:-true}"
export MACRO_RISK_OFF_SNIPER_ONLY="${MACRO_RISK_OFF_SNIPER_ONLY:-true}"
# Lane B — coach replay + slow auto-apply (live sniper/war unchanged)
export COACH_LANE_ENABLED="${COACH_LANE_ENABLED:-true}"
export COACH_SLOW_APPLY="${COACH_SLOW_APPLY:-true}"
export COMMANDER_REPLAY_ENABLED="${COMMANDER_REPLAY_ENABLED:-true}"
export COMMANDER_REPLAY_ON_SESSION_END="${COMMANDER_REPLAY_ON_SESSION_END:-true}"
export COACH_EVIDENCE_MIN_SESSIONS="${COACH_EVIDENCE_MIN_SESSIONS:-3}"
export COACH_EVIDENCE_MIN_TRIPS="${COACH_EVIDENCE_MIN_TRIPS:-4}"
export COACH_APPLY_MIN_INTERVAL_SEC="${COACH_APPLY_MIN_INTERVAL_SEC:-604800}"
export COACH_ROLLBACK_SESSIONS="${COACH_ROLLBACK_SESSIONS:-3}"
export COUNCIL_SCANNER_FAST_SEC="${COUNCIL_SCANNER_FAST_SEC:-1.5}"
export ENTRY_PENDING_LOOP_SEC="${ENTRY_PENDING_LOOP_SEC:-0.03}"
export ENTRY_PENDING_BLOCK_FAST_SEC="${ENTRY_PENDING_BLOCK_FAST_SEC:-8}"
export TICK_SPIKE_DEBOUNCE_SEC="${TICK_SPIKE_DEBOUNCE_SEC:-0.04}"
export GREEN_PROFIT_LOCK_AI_WAIT_SEC="${GREEN_PROFIT_LOCK_AI_WAIT_SEC:-1.5}"
# Override conservative discipline defaults when sniper is on
export CAPITAL_MIN_ENTRY_SCAN_SCORE="${CAPITAL_MIN_ENTRY_SCAN_SCORE:-40}"
export CAPITAL_MIN_ENTRY_SPIKE_RATIO="${CAPITAL_MIN_ENTRY_SPIKE_RATIO:-1.18}"
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.58}"
export MIN_PROFIT_PROBABILITY="${MIN_PROFIT_PROBABILITY:-0.58}"
export FLAT_LOOP_LOCKED_SEC="${FLAT_LOOP_LOCKED_SEC:-0.05}"
export RTH_MONITOR_SEC="${RTH_MONITOR_SEC:-0.05}"
export RTH_FLAT_LOOP_SEC="${RTH_FLAT_LOOP_SEC:-0.05}"
export AI_COUNCIL_MAX_WAIT_SEC="${AI_COUNCIL_MAX_WAIT_SEC:-1.5}"
# Sniper tick sensors — top 2 locked names on tick-by-tick; rest stay 5s (8GB-safe)
export SNIPER_TICK_STREAMS="${SNIPER_TICK_STREAMS:-true}"
export SNIPER_TICK_STREAM_COUNT="${SNIPER_TICK_STREAM_COUNT:-5}"
# Kill-fit lock pool — penny / mid / large tiers (score-first, not blue-chip only)
export SCAN_LOCK_DUAL_POOL="${SCAN_LOCK_DUAL_POOL:-true}"
export SCAN_LOCK_LARGE_MIN_SLOTS="${SCAN_LOCK_LARGE_MIN_SLOTS:-2}"
export SCAN_LOCK_MID_MIN_SLOTS="${SCAN_LOCK_MID_MIN_SLOTS:-2}"
export SCAN_LOCK_PENNY_MAX_SHARE="${SCAN_LOCK_PENNY_MAX_SHARE:-0.45}"
export SCAN_TIER_PENNY_MAX_PRICE="${SCAN_TIER_PENNY_MAX_PRICE:-5}"
export SCAN_TIER_MID_MAX_PRICE="${SCAN_TIER_MID_MAX_PRICE:-100}"
export SCAN_TIER_LARGE_MIN_PRICE="${SCAN_TIER_LARGE_MIN_PRICE:-100}"
export SCAN_KILL_FIT_LARGE_BONUS="${SCAN_KILL_FIT_LARGE_BONUS:-4}"
export SCAN_KILL_FIT_MID_BONUS="${SCAN_KILL_FIT_MID_BONUS:-2}"
export SCAN_SOFT_ROTATE_SEC="${SCAN_SOFT_ROTATE_SEC:-180}"
export SCAN_SOFT_ROTATE_DROP="${SCAN_SOFT_ROTATE_DROP:-2}"
export SCAN_SOFT_ROTATE_PROTECT="${SCAN_SOFT_ROTATE_PROTECT:-5}"
export SCAN_SOFT_ROTATE_SCORE_MARGIN="${SCAN_SOFT_ROTATE_SCORE_MARGIN:-5}"
export SCAN_MERGE_SEC="${SCAN_MERGE_SEC:-120}"
export SCAN_MERGE_MAX_SWAPS="${SCAN_MERGE_MAX_SWAPS:-1}"
export SCAN_TIER_PENNY_MIN_SPIKE="${SCAN_TIER_PENNY_MIN_SPIKE:-2.0}"
export SCAN_TIER_MID_MIN_SPIKE="${SCAN_TIER_MID_MIN_SPIKE:-1.6}"
export SCAN_TIER_MEGA_MIN_SPIKE="${SCAN_TIER_MEGA_MIN_SPIKE:-1.35}"
export SCAN_TIER_PENNY_MIN_SCORE="${SCAN_TIER_PENNY_MIN_SCORE:-70}"
export SCAN_TIER_MID_MIN_SCORE="${SCAN_TIER_MID_MIN_SCORE:-65}"
export SCAN_TIER_MEGA_MIN_SCORE="${SCAN_TIER_MEGA_MIN_SCORE:-60}"
export AI_TICK_STREAM_COUNT="${AI_TICK_STREAM_COUNT:-2}"
export PAPER_REALTIME_BARS_ONLY="${PAPER_REALTIME_BARS_ONLY:-false}"
# Yahoo SPY/QQQ/VIX — cached macro for council/Halim (advisory only, no entry veto)
export MACRO_CONTEXT_ENABLED="${MACRO_CONTEXT_ENABLED:-true}"
export MACRO_CONTEXT_REFRESH_SEC="${MACRO_CONTEXT_REFRESH_SEC:-600}"
export AI_PAPER_FREE_LEARNING="${AI_PAPER_FREE_LEARNING:-false}"
export LOTTERY_BANK_ENABLED="${LOTTERY_BANK_ENABLED:-false}"
export LOSS_STREAK_BLOCK_BYPASS_AT="${LOSS_STREAK_BLOCK_BYPASS_AT:-2}"
export AI_SPIKE_COOLDOWN_FAST_SEC="${AI_SPIKE_COOLDOWN_FAST_SEC:-20}"
# Live session gold collection (dialogue/copilot LM for training; user chat still off)
export HALIM_LIVE_GOLD_COLLECT="${HALIM_LIVE_GOLD_COLLECT:-true}"
export HALIM_ENTRY_LM_ENABLED="${HALIM_ENTRY_LM_ENABLED:-true}"
export HALIM_ENTRY_IB_CONTEXT="${HALIM_ENTRY_IB_CONTEXT:-true}"
export HALIM_ENTRY_BLEND_WEIGHT="${HALIM_ENTRY_BLEND_WEIGHT:-0.35}"
export HALIM_ENTRY_AWAIT_LIVE="${HALIM_ENTRY_AWAIT_LIVE:-true}"
export HALIM_ENTRY_AWAIT_SEC="${HALIM_ENTRY_AWAIT_SEC:-2.5}"
export HALIM_PPO_COMPLEMENT="${HALIM_PPO_COMPLEMENT:-true}"
export HALIM_OUTCOME_GOLD="${HALIM_OUTCOME_GOLD:-true}"
export HALIM_AUTO_INSTALL_COLAB="${HALIM_AUTO_INSTALL_COLAB:-true}"
export HALIM_PREPARE_SFT_ON_SHUTDOWN="${HALIM_PREPARE_SFT_ON_SHUTDOWN:-true}"
export HALIM_PPO_COEVOLUTION="${HALIM_PPO_COEVOLUTION:-true}"
export HALIM_PPO_TEACHER_VIA_HALIM="${HALIM_PPO_TEACHER_VIA_HALIM:-auto}"
export HALIM_PPO_TEACHER_TIMEOUT_SEC="${HALIM_PPO_TEACHER_TIMEOUT_SEC:-120}"
export HALIM_PPO_DIALOGUE="${HALIM_PPO_DIALOGUE:-true}"
export HALIM_PPO_GENERATIVE_REFLECT="${HALIM_PPO_GENERATIVE_REFLECT:-true}"
export HALIM_COMPANION_LEARN="${HALIM_COMPANION_LEARN:-true}"
export HALIM_ACTION_LEARN="${HALIM_ACTION_LEARN:-true}"
export HALIM_AUTO_PACKAGE_COLAB="${HALIM_AUTO_PACKAGE_COLAB:-true}"
export HALIM_LEARN_PACKAGE_ON_STOP="${HALIM_LEARN_PACKAGE_ON_STOP:-true}"
export LIVE_AI_PIPELINE_ENABLED="${LIVE_AI_PIPELINE_ENABLED:-true}"
export PYTHONUNBUFFERED=1
export LEARNING_PERSISTENCE_ENABLED="${LEARNING_PERSISTENCE_ENABLED:-true}"
export LEARNING_SYNC_INTERVAL_SEC="${LEARNING_SYNC_INTERVAL_SEC:-600}"

TOTAL_RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)

echo "═══════════════════════════════════════════════════════════════════════"
echo "  HANOON FULL PILOT LAUNCH"
echo "  IB: $IB_HOST:$IB_PORT | Client: $CLIENT_ID | Council: ${COUNCIL_BACKEND} (${TOTAL_RAM_MB}MB RAM)"
if [[ "${HALIM_LOW_MEMORY_ACTIVE:-}" == "true" ]]; then
  echo "  Halim: M. A. Halim low-RAM profile — MLX LM advisory, no live PPO train"
  echo "  Learning: capture only live | train off-hours | no mid-session PPO SSD writes"
fi
echo "  Clock: US Eastern (TZ=$TZ)"
echo "═══════════════════════════════════════════════════════════════════════"

# ── 1. Virtual environment ────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "📦 Creating Python venv..."
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

if [ -f "requirements.txt" ]; then
  pip install -q -r requirements.txt 2>/dev/null || pip install -r requirements.txt
fi

# ── 2. Environment + encrypted vault (cross-device) ─────────────────────
python3 -c "
from core.env_secrets import bootstrap_env
ok, msg = bootstrap_env('$ROOT')
print('✅' if ok else '⚠️ ', msg)
" 2>/dev/null || true

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "✅ Loaded .env"
elif [ -f "secrets/hanoon.env.enc" ]; then
  echo "⚠️  Run: pip install cryptography && restart (vault needs decrypt)"
elif [ -f ".env.example" ]; then
  echo "⚠️  No .env — git pull or copy .env.example"
fi

# Learning posture — applied AFTER .env so these win over stale .env values
export AI_LEARN_DONT_BLOCK="${AI_LEARN_DONT_BLOCK:-true}"
export AI_LEARN_ON_LOSS_STREAK="${AI_LEARN_ON_LOSS_STREAK:-true}"
export INCREMENTAL_TRAINING_ENABLED=false
export AI_RUNTIME_OBSERVER_ENABLED="${AI_RUNTIME_OBSERVER_ENABLED:-true}"
export AI_RUNTIME_AUTO_APPLY="${AI_RUNTIME_AUTO_APPLY:-true}"
export OLLAMA_VISION_SWAP_TEXT_MODEL=false
export CHART_VISION_ENTRY_ONLY="${CHART_VISION_ENTRY_ONLY:-true}"
export LIVE_CHART_VISION_OPPORTUNISTIC="${LIVE_CHART_VISION_OPPORTUNISTIC:-true}"
export CHART_VISION_OPPORTUNISTIC_COOLDOWN_SEC="${CHART_VISION_OPPORTUNISTIC_COOLDOWN_SEC:-120}"
export GIT_NOTIFY_MODE="${GIT_NOTIFY_MODE:-off}"
export TELEGRAM_BROADCAST_GIT="${TELEGRAM_BROADCAST_GIT:-false}"
export OFF_HOURS_HEAVY_TRAINING="${OFF_HOURS_HEAVY_TRAINING:-true}"
export LOSS_STREAK_LEARNING_MIN_SEC="${LOSS_STREAK_LEARNING_MIN_SEC:-45}"
export LOSS_STREAK_LEARNING_MAX_SEC="${LOSS_STREAK_LEARNING_MAX_SEC:-300}"
export LOSS_STREAK_RESUME_CONFIDENCE="${LOSS_STREAK_RESUME_CONFIDENCE:-0.52}"
export SCAN_RUN_DEFERRED_IB="${SCAN_RUN_DEFERRED_IB:-true}"
export SCAN_DEFER_IB_ON_STARTUP="${SCAN_DEFER_IB_ON_STARTUP:-false}"
export OFF_HOURS_SUSPEND_MARKET_DATA="${OFF_HOURS_SUSPEND_MARKET_DATA:-true}"
export USE_TICK_STREAM="${USE_TICK_STREAM:-true}"
export TICK_BY_TICK_TYPE="${TICK_BY_TICK_TYPE:-AllLast}"
export HMDS_FETCH_TIMEOUT_SEC="${HMDS_FETCH_TIMEOUT_SEC:-12}"
echo "✅ Learning posture: loss_streak=on incremental_train=off runtime_observer=on | paper_md=5s-bars | live_tick=${USE_TICK_STREAM} | boot_scan=live_ib"

# ── 2b. GitHub CLI (releases + artifact sync) ───────────────────────────────
ensure_gh() {
  if ! command -v gh >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
      echo "📦 Installing GitHub CLI (gh)..."
      brew install gh >>"$LOG_DIR/gh.log" 2>&1 || true
    fi
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "⚠️  gh not installed — GitHub releases disabled (brew install gh)"
    return 0
  fi
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    export GH_TOKEN="$GITHUB_TOKEN"
    export GITHUB_TOKEN
    if ! gh auth status >/dev/null 2>&1; then
      echo "🔐 Authenticating gh with GITHUB_TOKEN..."
      printf '%s\n' "$GITHUB_TOKEN" | gh auth login --with-token >>"$LOG_DIR/gh.log" 2>&1 || true
    fi
    gh auth setup-git >>"$LOG_DIR/gh.log" 2>&1 || true
    echo "✅ GitHub CLI ready"
  else
    echo "⚠️  GITHUB_TOKEN not set in .env — gh installed but releases need a token"
  fi
}
ensure_gh

# ── 3. Cloud council keys ───────────────────────────────────────────────────
GROQ_KEY_COUNT=0
if [ -n "${GROQ_API_KEY:-}" ]; then
  GROQ_KEY_COUNT=1
  [ -n "${GROQ_API_KEY_2:-}" ] && GROQ_KEY_COUNT=$((GROQ_KEY_COUNT + 1))
  echo "✅ Groq API key(s) loaded ($GROQ_KEY_COUNT account(s) — round-robin for higher limits)"
else
  echo "⚠️  GROQ_API_KEY not set — council will use Gemini only (if configured)"
fi
if [ -n "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}" ]; then
  echo "✅ Google/Gemini API key loaded (fallback + chart vision)"
else
  echo "⚠️  GEMINI_API_KEY not set — no Gemini fallback or chart vision"
fi

# ── 4. Stop stale bot instances ─────────────────────────────────────────────
if pgrep -f "main.py --mode scalper" >/dev/null 2>&1; then
  echo "🛑 Stopping previous scalper instance (graceful)..."
  "$ROOT/scripts/stop_hanoon.sh" || true
  sleep 2
fi

mkdir -p "$ROOT/runtime"
rm -f "$ROOT/runtime/shutdown.request"

# ── 5. IB Gateway port check ────────────────────────────────────────────────
if command -v nc >/dev/null 2>&1; then
  if nc -z "$IB_HOST" "$IB_PORT" 2>/dev/null; then
    echo "✅ IB Gateway port $IB_PORT is open"
  else
    echo "⚠️  IB Gateway not detected on $IB_HOST:$IB_PORT"
    echo "    Start IB Gateway (paper) and log in before trading"
  fi
fi

# ── 6. Pre-flight validation ────────────────────────────────────────────────
echo "🚦 Quick pre-flight..."
python3 -c "
from core.config import BotConfig
import os
cfg = BotConfig()
model = cfg.MODEL_PATH
print(f'   Model: {model} ({\"found\" if os.path.exists(model) else \"MISSING\"})')
if os.getenv('SKIP_PREFLIGHT_FEATURE_VALIDATE', 'true').lower() not in ('0', 'false', 'no'):
    print('   Features: deferred (DEFER_FEATURE_VALIDATION=true)')
else:
    from core.feature_drift import validate_features_at_startup
    from core.features_enhanced import FeatureEngineerEnhanced
    fe = FeatureEngineerEnhanced()
    ok = validate_features_at_startup(lambda df, window_size=30: fe.compute(df))
    print(f'   Features: {\"PASS\" if ok else \"WARN\"}')
print(f'   Pilot mode: {getattr(cfg, \"PILOT_MODE_ENABLED\", True)}')
print(f'   Live IB scanner: {getattr(cfg, \"USE_LIVE_IB_SCANNER\", True)} (no static fallback)')
print(f'   Fast scanner lock: {getattr(cfg, \"FAST_SCANNER_LOCK\", True)} (bars prefetch after lock)')
print(f'   AI full control: {getattr(cfg, \"AI_FULL_CONTROL\", True)} | distill fast-path: {getattr(cfg, \"HYBRID_DISTILL_AUTO_FAST_PATH\", True)}')
print(f'   AI council all decisions: {getattr(cfg, \"AI_COUNCIL_ALL_DECISIONS\", True)}')
from core.ai_session_limits import should_ai_define_limits, heuristic_session_limits, apply_session_limits, format_limits_log
if should_ai_define_limits(cfg):
    eq = float(os.getenv('PAPER_EQUITY_HINT', '0') or 0) or float(getattr(cfg, 'INITIAL_CASH', 1000))
    lim = heuristic_session_limits(cfg, eq)
    apply_session_limits(cfg, lim)
    print(f'   {format_limits_log(cfg, eq)}')
    print(f'   Full capital access: {getattr(cfg, \"AI_FULL_CAPITAL_ACCESS\", True)} | defer bar warm: {getattr(cfg, \"DEFER_BAR_WARM_ON_LOCK\", True)}')
else:
    print(f'   AI unlimited: {getattr(cfg, \"AI_UNLIMITED_MODE\", False)} | Watch pool: {getattr(cfg, \"MAX_LOCKED_TARGETS\", 5)} | Max positions: {getattr(cfg, \"MAX_CONCURRENT_POSITIONS\", 5)}')
    print(f'   Fixed deploy cap: {getattr(cfg, \"USE_FIXED_DEPLOY_CAP\", False)} | Fixed risk cap: {getattr(cfg, \"USE_FIXED_RISK_CAP\", False)} | Account halt: {getattr(cfg, \"USE_ACCOUNT_LOSS_HALT\", False)}')
groq = bool(getattr(cfg, 'GROQ_API_KEY', ''))
gem = bool(getattr(cfg, 'GEMINI_API_KEY', '') or getattr(cfg, 'GOOGLE_API_KEY', ''))
print(f'   Council: {getattr(cfg, \"COUNCIL_ENABLED\", False)} | backend={getattr(cfg, \"COUNCIL_BACKEND\", \"groq\")} | groq_key={\"yes\" if groq else \"no\"} | gemini_key={\"yes\" if gem else \"no\"}')
print(f'   Groq model: {getattr(cfg, \"GROQ_MODEL\", \"?\")} | Gemini: {getattr(cfg, \"GEMINI_MODEL\", \"?\")}')
print(f'   Learn live: AI_LEARN_ON_LOSS_STREAK={getattr(cfg, \"AI_LEARN_ON_LOSS_STREAK\", False)} | INCREMENTAL_TRAINING={getattr(cfg, \"INCREMENTAL_TRAINING_ENABLED\", True)} | runtime_observer={getattr(cfg, \"AI_RUNTIME_OBSERVER_ENABLED\", True)}')
from core.ollama_vision import is_vision_model_present, vision_model_name
vm = vision_model_name(cfg)
print(f'   Chart vision ({vm}): {\"ready\" if is_vision_model_present(cfg) else \"needs GEMINI_API_KEY\"}')
print(f'   Telegram listen: {getattr(cfg, \"TELEGRAM_LISTEN_ENABLED\", True)} | verify secret: {\"set\" if getattr(cfg, \"TELEGRAM_VERIFY_SECRET\", \"\") else \"MISSING\"}')
from core.git_sync import ensure_github_cli
gh_ok = ensure_github_cli(cfg)
print(f'   GitHub CLI: {\"ready\" if gh_ok else \"WARN\"}')
try:
    from core.war_account import ensure_war_account, war_account_enabled
    if war_account_enabled(cfg):
        st = ensure_war_account(cfg)
        print(f'   War: nav={float(st.get(\"nav\", 0)):,.0f} settled={float(st.get(\"settled_cash\", 0)):,.0f} mode={st.get(\"mode\", \"?\")}')
except Exception as e:
    print(f'   War: check skipped ({e})')
try:
    from core.market_context import refresh_macro_context, macro_context_enabled
    if macro_context_enabled():
        m = refresh_macro_context(force=False)
        if m.get('source') not in ('unavailable', 'error'):
            print(f'   Macro: SPY {m.get(\"spy_pct\", 0):+.2f}% QQQ {m.get(\"qqq_pct\", 0):+.2f}% VIX {m.get(\"vix_level\", 0):.1f} ({m.get(\"risk_tone\", \"?\")})')
        else:
            print('   Macro: warming on startup (Yahoo)')
except Exception as e:
    print(f'   Macro: skipped ({e})')
try:
    from core.sniper_execution import sniper_timing_log_line, sniper_active
    from core.config import BotConfig as _BC
    if sniper_active(_BC()):
        print(f'   {sniper_timing_log_line(_BC())}')
except Exception:
    pass
" 2>&1 || echo "   Pre-flight warnings (non-fatal)"

# ── 6a. Halim serve — auto-install Colab zip if new, then always active ──
if [[ "${HALIM_AUTO_INSTALL_COLAB:-true}" == "true" ]]; then
  echo "📦 Checking for new Colab Halim checkpoint (Downloads / Drive)…"
  "$ROOT/scripts/halim_apply_colab_checkpoint.sh" --if-new 2>/dev/null || true
fi
if [ "${TRADING_BOT_TELEGRAM_LISTEN:-true}" = "true" ]; then
  "$ROOT/scripts/halim_stop.sh" --telegram-only 2>/dev/null || true
fi
echo ""
echo "🧠 Ensuring Halim serve is active (fresh code on each HANOON start)…"
"$ROOT/scripts/ensure_halim_active.sh" --serve-only --restart || echo "   Halim serve warning (non-fatal — see logs/halim_serve.log)"

if [ "${HALIM_STANDALONE_WATCHDOG:-true}" = "true" ]; then
  echo "🛡️ Starting Halim serve watchdog (keeps :8765 alive during trading)…"
  "$ROOT/scripts/start_halim_watchdog.sh" || echo "   Halim watchdog warning (see logs/halim_watchdog.log)"
fi

export CONNECTIVITY_WAIT_ON_IB_LOSS="${CONNECTIVITY_WAIT_ON_IB_LOSS:-true}"
export RECONNECT_MAX_ATTEMPTS_LIVE="${RECONNECT_MAX_ATTEMPTS_LIVE:-0}"
export RECONNECT_WAIT_LOG_EVERY="${RECONNECT_WAIT_LOG_EVERY:-10}"
export IB_GATEWAY_WATCHDOG_ENABLED="${IB_GATEWAY_WATCHDOG_ENABLED:-true}"
export IB_GATEWAY_WATCHDOG_INTERVAL_SEC="${IB_GATEWAY_WATCHDOG_INTERVAL_SEC:-30}"

if [ "${IB_GATEWAY_WATCHDOG_ENABLED}" = "true" ]; then
  echo "🛡️ Starting IB Gateway watchdog (monitors ${IB_HOST}:${IB_PORT})…"
  "$ROOT/scripts/start_ib_gateway_watchdog.sh" || echo "   IB Gateway watchdog warning (see logs/ib_gateway_watchdog.log)"
fi

# ── 6b. Standalone git sync (off by default during live — competes with IB loop) ───
if [ "${START_GIT_SYNC_WITH_HANOON:-false}" = "true" ]; then
  echo ""
  echo "📤 Starting git sync daemon (auto-pushes all file changes)..."
  "$ROOT/scripts/start_git_sync.sh" || echo "   Git sync start skipped (see logs/git_sync.log)"
else
  echo ""
  echo "📤 Git sync daemon skipped (START_GIT_SYNC_WITH_HANOON=false) — pushes on stop_hanoon only"
fi

echo ""
echo "📋 IDE / editor: save any file → git sync pushes within ~${GIT_AUTO_PUSH_INTERVAL_SEC:-12}s"
echo "   (works in Cursor, VS Code, PyCharm — no plugin needed)"
echo "   Secrets: .env stays local; encrypted vault syncs via secrets/hanoon.env.enc"
echo ""

# ── 7. Launch HANOON scalper ────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 HANOON LIFE ENGINE — scalp + swing · IB Truth"
echo "  Log: $MAIN_LOG"
echo "  Graceful stop: ./stop.sh  (Halim gold + evolution + git — not Ctrl+C)"
echo "  Avoid Ctrl+C — it skips evolution and git sync"
echo "  Live tail: tail -f $MAIN_LOG"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Foreground: StreamHandler → terminal, FileHandler → $MAIN_LOG (see core/notify.py)
PY="${ROOT}/venv/bin/python3"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" -u main.py --mode scalper --port "$IB_PORT" --client-id "$CLIENT_ID"
