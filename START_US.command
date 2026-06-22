#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# START_US.command — HA-NUN US Market Instance (SPY, 1min scalper)
# ════════════════════════════════════════════════════════════════════════════════
# Opens a dedicated terminal for US market trading.
# Uses client-id 1 (must differ from LSE instance).
# Shares the same brain/weights as LSE instance.
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════════════════════════════════"
echo "  🇺🇸 HA-NUN US MARKET INSTANCE"
echo "  Asset: SPY | Exchange: SMART | Ccy: USD"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# ── 1. Environment ───────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run START.command first."
    read -p "Press Enter to exit..."
    exit 1
fi

echo "🔄 Activating virtual environment..."
source venv/bin/activate

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# ── 2. Git sync (pull latest brain weights) ─────────────────────────────────
echo "📡 Syncing shared brain weights from GitHub..."
git fetch origin main 2>/dev/null || true
git reset --hard origin/main 2>/dev/null || echo "  ℹ️  No remote changes"

# ── 3. Launch US scalper ────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 LAUNCHING US MARKET INSTANCE"
echo "  Asset: SPY | Timeframe: 1min | Port: 7497 | Client-ID: 1"
echo "  IB Gateway must be running (4002/7497)"
echo "  Press Ctrl+C to stop this instance only"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 main.py --mode scalper \
    --ticker SPY \
    --timeframe 1min \
    --port 7497 \
    --client-id 1 2>&1 | tee HA-NUN_US.log

echo ""
echo "🇺🇸 US Market instance stopped."
read -p "Press Enter to close..."