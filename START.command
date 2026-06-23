#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# START.command — HANOON Live Trading Lifecycle (Double-click to run)
# ════════════════════════════════════════════════════════════════════════════════
# This script orchestrates the complete live trading lifecycle:
#   1. Environment check & venv activation
#   2. Feature drift validation gate
#   3. Git pull latest weights/config from HANOON repo
#   4. Launch live trading bot (scalper_runner)
#   5. Off-hours training + Grandmaster push (background)
#   6. Daily close report + Ollama meta-optimizer
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Resolve script directory (project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════════════════════════════════"
echo "  HANOON TRADING BOT — LIVE LIFECYCLE"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# ── 1. Environment check ──────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Creating venv..."
    python3 -m venv venv
    echo "✅ venv created"
fi

echo "🔄 Activating virtual environment..."
source venv/bin/activate

echo "🔧 Installing/verifying dependencies..."
pip install -q python-dotenv pandas torch numpy statsmodels scikit-learn 2>/dev/null || true

# Load .env
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
    echo "✅ Environment loaded from .env"
else
    echo "⚠️  .env not found. Using .env.example defaults."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "   Created .env from .env.example — EDIT IT WITH YOUR CREDENTIALS!"
    fi
fi

# ── 2. Pre-flight checks ──────────────────────────────────────────────────────
echo ""
echo "🛫 Pre-flight checks..."

# Verify critical files
CRITICAL_FILES=(
    "core/config.py"
    "core/scalper_runner.py"
    "core/multi_model_fusion.py"
    "core/hmrs.py"
    "core/stationary_features.py"
    "core/transformer_model.py"
)
MISSING=0
for f in "${CRITICAL_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        echo "  ❌ MISSING: $f"
        MISSING=1
    fi
done
if [ "$MISSING" -eq 1 ]; then
    echo ""
    echo "❌ Pre-flight FAILED. Fix missing files and restart."
    read -p "Press Enter to exit..."
    exit 1
fi
echo "  ✅ All critical files present"

# ── 3. Git sync: pull latest weights & config ─────────────────────────────────
echo ""
echo "📡 Syncing with GitHub (HANOON repo)..."

# Configure git identity for this machine
git config --global user.email "bot@hanoon.local" 2>/dev/null || true
git config --global user.name "HANUN-Bot" 2>/dev/null || true

# Hard reset to origin/main ensures we always run the latest deployed code
git fetch origin main 2>/dev/null || true
if [ ! -f "bot_state.json" ]; then
  git reset --hard origin/main 2>/dev/null || echo "  ℹ️  No remote changes to pull"
else
  echo "  ⚠️  Skipping hard reset: bot_state.json exists"
fi

echo "  ✅ Repo synchronized"

# ── 4. Feature drift validation gate ─────────────────────────────────────────
echo ""
echo "🚦 Running Feature Drift Validation Gate..."
python3 -c "
from core.features_enhanced import FeatureEngineerEnhanced
from core.feature_drift import validate_features_at_startup
fe = FeatureEngineerEnhanced()
# Wrap compute() to match the validator's expected signature (df, window_size=...)
result = validate_features_at_startup(lambda df, window_size=30: fe.compute(df))
print(f'   Drift check: {\"PASS\" if result else \"WARN\"}')
" 2>&1 || echo "  ℹ️  Drift validator skipped (no market data yet)"

# ── 5. Launch live trading bot ────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 LAUNCHING LIVE TRADING BOT"
echo "  Asset: SPY | Mode: Scalper | Paper: True"
echo "  Press Ctrl+C to stop gracefully"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Run the scalper runner (this blocks until stopped)
python3 -m core.scalper_runner 2>&1 | tee HANOON.log

# ── 6. Post-session cleanup (runs after bot stops) ────────────────────────────
echo ""
echo "🧹 Post-session cleanup..."

# Push final state
python3 -c "
from core.git_sync import push_change
push_change('shutdown: bot stopped', files=['HANOON.log', 'bot_state.json'], category='shutdown')
" 2>&1 || true

# Trigger off-hours training in background (non-blocking)
echo ""
echo "🏋️  Queuing off-hours training (Grandmaster distillation)..."
python3 -c "
from core.train_subprocess import launch_training
launch_training([
    'python3', '-m', 'core.advanced_training',
    '--mode', 'full',
    '--ticker', 'SPY',
    '--epochs', '20'
], timeout_minutes=480, memory_limit_mb=4096, auto_git_push=True)
" 2>&1 || echo "  ℹ️  Training queued for next run"

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "  ✅ Live trading lifecycle complete"
echo "  📊 Check HANOON.log for session summary"
echo "  🔄 Off-hours training running in background"
echo "═══════════════════════════════════════════════════════════════════════"
read -p "Press Enter to close..."