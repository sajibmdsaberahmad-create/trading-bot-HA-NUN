#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# START_LSE.command — HA-NUN LSE Market Instance (ISF, 1min scalper)
# ════════════════════════════════════════════════════════════════════════════════
# Opens a dedicated terminal for London Stock Exchange trading.
# Uses client-id 2 (must differ from US instance).
# Shares the same brain/weights as US instance.
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════════════════════════════════"
echo "  🇬🇧 HA-NUN LSE MARKET INSTANCE"
echo "  Asset: ISF | Exchange: LSE | Ccy: GBP"
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

# ── 2. Git sync (pull latest brain weights — same repo) ─────────────────────
echo "📡 Syncing shared brain weights from GitHub..."
git fetch origin main 2>/dev/null || true
git reset --hard origin/main 2>/dev/null || echo "  ℹ️  No remote changes"

# ── 3. Launch LSE scalper ───────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 LAUNCHING LSE MARKET INSTANCE"
echo "  Asset: ISF (FTSE 100) | Timeframe: 1h | Client-ID: 2"
echo "  IB Gateway must be running with UK market data subscription"
echo "  Press Ctrl+C to stop this instance only"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 main.py --mode scalper \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --client-id 2 2>&1 | tee HA-NUN_LSE.log

echo ""
echo "🇬🇧 LSE Market instance stopped."
read -p "Press Enter to close..."