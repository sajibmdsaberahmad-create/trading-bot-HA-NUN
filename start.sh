#!/usr/bin/env bash
# start.sh — One-command start: force-clean RAM + Halim serve (bg) + scalper (fg)
set -euo pipefail
cd "$(dirname "$0")"

# ── Force-clean all non-essential apps for max RAM ─────────────────────────
echo "🧹 Force-closing non-essential apps to free RAM..."
echo "   (Cursor, browsers, office, media, utilities)"
echo ""
bash scripts/max_perf.sh
echo ""

source venv/bin/activate
source scripts/m2_8gb_live_profile.sh

echo "🚀 Starting Halim serve (background)..."
nohup python3 -u halim/halim/serve.py > logs/halim_serve_daemon.log 2>&1 &
HALIM_PID=$!
echo "   Halim PID=$HALIM_PID"
sleep 2

echo "🚀 Starting HANOON scalper..."
echo "   Stop with Ctrl+C then run: ./stop.sh"
echo ""
python3 -u main.py --mode scalper --port 4002 --client-id 1

echo "🛑 Scalper exited. Run ./stop.sh to clean up Halim serve."
