#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# scripts/start_hanoon.sh — One command starts EVERYTHING for HANOON pilot mode
#
# Orchestrates:
#   1. venv + dependencies
#   2. Ollama (serve + model pull) for generative AI
#   3. Pre-flight checks (features, model, IB port)
#   4. Stale process cleanup
#   5. HANOON scalper (live IB scanner, pilot mode, cognitive autopilot)
#
# Usage:
#   ./scripts/start_hanoon.sh
#   IB_PORT=4002 CLIENT_ID=2 ./scripts/start_hanoon.sh
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

# Pick lowest free IB client ID (1–10). Override with CLIENT_ID env var.
pick_client_id() {
  local id
  for id in $(seq 1 10); do
    if ! pgrep -f "main.py.*--client-id[ =]${id}([ ^]|$)" >/dev/null 2>&1; then
      echo "$id"
      return
    fi
  done
  echo 1
}
CLIENT_ID="${CLIENT_ID:-$(pick_client_id)}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

# Auto-pick model for 8GB with 2.5GB Ollama budget (override with OLLAMA_MODEL env)
TOTAL_RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)
export OLLAMA_MEMORY_BUDGET_MB="${OLLAMA_MEMORY_BUDGET_MB:-2560}"
    if [ -z "${OLLAMA_MODEL:-}" ]; then
  if [ "$TOTAL_RAM_MB" -le 10240 ]; then
    OLLAMA_MODEL="qwen2.5:3b"
  else
    OLLAMA_MODEL="llama3"
  fi
fi
export OLLAMA_MODEL
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-600}"
export OLLAMA_UNLOAD_AFTER_CALL="${OLLAMA_UNLOAD_AFTER_CALL:-false}"
export OLLAMA_MIN_CALL_INTERVAL_SEC="${OLLAMA_MIN_CALL_INTERVAL_SEC:-1}"
export OLLAMA_DECISION_MIN_FREE_RAM_MB="${OLLAMA_DECISION_MIN_FREE_RAM_MB:-768}"
export OLLAMA_MIN_FREE_RAM_MB="${OLLAMA_MIN_FREE_RAM_MB:-1024}"
export OLLAMA_NUM_CTX="${OLLAMA_NUM_CTX:-2048}"
export OLLAMA_MAX_TOKENS="${OLLAMA_MAX_TOKENS:-256}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
MAIN_LOG="$LOG_DIR/HANOON.log"
OLLAMA_LOG="$LOG_DIR/ollama.log"
PID_FILE="$LOG_DIR/hanoon.pid"

mkdir -p "$LOG_DIR" "$ROOT/models/daily_reports" "$ROOT/runtime"

export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"
export OLLAMA_HOST
export OLLAMA_ENABLED="${OLLAMA_ENABLED:-true}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export PYTHONUNBUFFERED=1

echo "═══════════════════════════════════════════════════════════════════════"
echo "  HANOON FULL PILOT LAUNCH"
echo "  IB: $IB_HOST:$IB_PORT | Client: $CLIENT_ID | Ollama: $OLLAMA_MODEL (${TOTAL_RAM_MB}MB RAM)"
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

# ── 2. Environment file ─────────────────────────────────────────────────────
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "✅ Loaded .env"
elif [ -f ".env.example" ]; then
  echo "⚠️  No .env — copy .env.example to .env and add your keys"
fi

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

# ── 3. Ollama (generative thinking) ─────────────────────────────────────────
start_ollama() {
  if ! command -v ollama >/dev/null 2>&1; then
    echo "⚠️  Ollama not installed — generative AI disabled"
    echo "    Install: https://ollama.com/download"
    export OLLAMA_ENABLED=false
    return 0
  fi

  if ! curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    echo "🧠 Starting Ollama server..."
    if command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -q ollama; then
      brew services start ollama >>"$OLLAMA_LOG" 2>&1 || true
    else
      nohup ollama serve >>"$OLLAMA_LOG" 2>&1 &
      echo $! >"$LOG_DIR/ollama.pid"
    fi
    for i in $(seq 1 15); do
      if curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
        echo "✅ Ollama server ready"
        break
      fi
      sleep 1
    done
  else
    echo "✅ Ollama already running"
  fi

  if curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    if ollama list 2>/dev/null | grep -qE "^${OLLAMA_MODEL}([[:space:]:]|$)"; then
      echo "✅ Ollama model ready: $OLLAMA_MODEL"
    else
      echo "📥 Pulling $OLLAMA_MODEL in background (bot starts now)..."
      nohup ollama pull "$OLLAMA_MODEL" >>"$OLLAMA_LOG" 2>&1 &
    fi
    export OLLAMA_ENABLED=true
  else
    echo "⚠️  Ollama unreachable — continuing without generative AI"
    export OLLAMA_ENABLED=false
  fi
}
start_ollama

# ── 4. Stop stale bot instances ─────────────────────────────────────────────
if pgrep -f "main.py --mode scalper" >/dev/null 2>&1; then
  echo "🛑 Stopping previous scalper instance..."
  pkill -f "main.py --mode scalper" 2>/dev/null || true
  sleep 2
fi

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
echo "🚦 Feature + model validation..."
python3 -c "
from core.config import BotConfig
from core.feature_drift import validate_features_at_startup
from core.features_enhanced import FeatureEngineerEnhanced
import os
cfg = BotConfig()
fe = FeatureEngineerEnhanced()
ok = validate_features_at_startup(lambda df, window_size=30: fe.compute(df))
model = cfg.MODEL_PATH
print(f'   Model: {model} ({\"found\" if os.path.exists(model) else \"MISSING\"})')
print(f'   Features: {\"PASS\" if ok else \"WARN\"}')
print(f'   Pilot mode: {getattr(cfg, \"PILOT_MODE_ENABLED\", True)}')
print(f'   Live IB scanner: {getattr(cfg, \"USE_LIVE_IB_SCANNER\", True)} (no static fallback)')
print(f'   Ollama: {getattr(cfg, \"OLLAMA_ENABLED\", False)}')
from core.git_sync import ensure_github_cli
gh_ok = ensure_github_cli(cfg)
print(f'   GitHub CLI: {\"ready\" if gh_ok else \"WARN\"}')
" 2>&1 || echo "   Pre-flight warnings (non-fatal)"

# ── 7. Launch HANOON scalper ────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 HANOON SCALPER — FULL PILOT MODE"
echo "  Log: $MAIN_LOG"
echo "  Press Ctrl+C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo $$ >"$PID_FILE"
exec python3 main.py --mode scalper --port "$IB_PORT" --client-id "$CLIENT_ID" 2>&1 | tee -a "$MAIN_LOG"
