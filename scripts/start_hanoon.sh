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
if pgrep -f "main.py.*--client-id[ =]${CLIENT_ID}([ ^]|$)" >/dev/null 2>&1; then
  echo "⚠️  Another process already uses IB client_id=${CLIENT_ID} — stop it first (./stop.sh)"
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

mkdir -p "$LOG_DIR" "$ROOT/models/daily_reports" "$ROOT/runtime"

export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"
export CAPITAL_DISCIPLINE="${CAPITAL_DISCIPLINE:-true}"
export TREAT_PAPER_AS_LIVE="${TREAT_PAPER_AS_LIVE:-true}"
export AI_SPIKE_FAST_ENTRY="${AI_SPIKE_FAST_ENTRY:-false}"
export PPO_LEAD_WHILE_COUNCIL_PENDING="${PPO_LEAD_WHILE_COUNCIL_PENDING:-false}"
export MIN_PROFIT_PROBABILITY="${MIN_PROFIT_PROBABILITY:-0.62}"
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.65}"
export ENTRY_QUALITY_BLEND_WEIGHT="${ENTRY_QUALITY_BLEND_WEIGHT:-0.55}"
export ENTRY_QUALITY_HARDNESS="${ENTRY_QUALITY_HARDNESS:-0.45}"
export CAPITAL_MIN_ENTRY_SCAN_SCORE="${CAPITAL_MIN_ENTRY_SCAN_SCORE:-55}"
export CAPITAL_MIN_ENTRY_SPIKE_RATIO="${CAPITAL_MIN_ENTRY_SPIKE_RATIO:-1.25}"
export CAPITAL_ENTRY_COOLDOWN_SEC="${CAPITAL_ENTRY_COOLDOWN_SEC:-0}"
export MAX_ENTRIES_PER_HOUR="${MAX_ENTRIES_PER_HOUR:-0}"
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
export AI_FULL_CAPITAL_ACCESS="${AI_FULL_CAPITAL_ACCESS:-true}"
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
export PPO_LEAD_WHILE_COUNCIL_PENDING="${PPO_LEAD_WHILE_COUNCIL_PENDING:-true}"
export PPO_LEARN_EVERY_ENTRY="${PPO_LEARN_EVERY_ENTRY:-true}"
export PPO_ENTRY_MICRO_STEPS="${PPO_ENTRY_MICRO_STEPS:-512}"
export AI_STREAM_WATCH_CAP="${AI_STREAM_WATCH_CAP:-10}"
export AI_STREAM_PRIORITY_COUNT="${AI_STREAM_PRIORITY_COUNT:-6}"
export SCALP_PROFIT_GIVEBACK_PCT="${SCALP_PROFIT_GIVEBACK_PCT:-0.20}"
export TRAILING_PROFIT_GIVEBACK_PCT="${TRAILING_PROFIT_GIVEBACK_PCT:-0.25}"
export IN_PROFIT_MANAGE_PNL_PCT="${IN_PROFIT_MANAGE_PNL_PCT:-0.002}"
export LOCK_BAR_REFRESH_SEC="${LOCK_BAR_REFRESH_SEC:-90}"
export LOCK_STALE_RELEASE_SEC="${LOCK_STALE_RELEASE_SEC:-600}"
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
export USE_ACCOUNT_LOSS_HALT="${USE_ACCOUNT_LOSS_HALT:-false}"
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
export LIVE_AI_PIPELINE_ENABLED="${LIVE_AI_PIPELINE_ENABLED:-true}"
export PYTHONUNBUFFERED=1
export LEARNING_PERSISTENCE_ENABLED="${LEARNING_PERSISTENCE_ENABLED:-true}"
export LEARNING_SNAPSHOT_INTERVAL_SEC="${LEARNING_SNAPSHOT_INTERVAL_SEC:-300}"
export LEARNING_SYNC_INTERVAL_SEC="${LEARNING_SYNC_INTERVAL_SEC:-600}"

TOTAL_RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)

echo "═══════════════════════════════════════════════════════════════════════"
echo "  HANOON FULL PILOT LAUNCH"
echo "  IB: $IB_HOST:$IB_PORT | Client: $CLIENT_ID | Council: ${COUNCIL_BACKEND} (${TOTAL_RAM_MB}MB RAM)"
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
" 2>&1 || echo "   Pre-flight warnings (non-fatal)"

# ── 6b. Standalone git sync (auto-push any IDE save — separate process) ───
if [ "${START_GIT_SYNC_WITH_HANOON:-true}" = "true" ]; then
  echo ""
  echo "📤 Starting git sync daemon (auto-pushes all file changes)..."
  "$ROOT/scripts/start_git_sync.sh" || echo "   Git sync start skipped (see logs/git_sync.log)"
fi

echo ""
echo "📋 IDE / editor: save any file → git sync pushes within ~${GIT_AUTO_PUSH_INTERVAL_SEC:-12}s"
echo "   (works in Cursor, VS Code, PyCharm — no plugin needed)"
echo "   Secrets: .env stays local; encrypted vault syncs via secrets/hanoon.env.enc"
echo ""

# ── 7. Launch HANOON scalper ────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 HANOON SCALPER — FULL PILOT MODE"
echo "  Log: $MAIN_LOG"
echo "  Graceful stop: ./stop.sh  (Halim gold + evolution + git — not Ctrl+C)"
echo "  Avoid Ctrl+C — it skips evolution and git sync"
echo "  Live tail: tail -f $MAIN_LOG"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Foreground + tee: live terminal output; bot writes logs/hanoon.pid via write_pid()
python3 -u main.py --mode scalper --port "$IB_PORT" --client-id "$CLIENT_ID" 2>&1 | tee -a "$MAIN_LOG"
