#!/usr/bin/env bash
# start.sh — One-command start: force-clean RAM + fresh Halim serve (bg) + scalper (fg)
set -euo pipefail
cd "$(dirname "$0")"

# ── Force-clean all non-essential apps for max RAM ─────────────────────────
echo "🧹 Force-closing non-essential apps to free RAM..."
echo "   (Cursor, browsers, office, media, utilities)"
echo ""
bash scripts/max_perf.sh || echo "   (max_perf cleanup non-critical, continuing...)"
echo ""

# ── Kill any stale Halim serve (old process accumulates memory issues) ─────
OLD_HALIM=$(pgrep -f "halim/halim/serve.py" 2>/dev/null || true)
if [ -n "$OLD_HALIM" ]; then
    echo "🔄 Killing stale Halim serve (PID $OLD_HALIM)..."
    kill "$OLD_HALIM" 2>/dev/null || true
    sleep 2
    kill -0 "$OLD_HALIM" 2>/dev/null && kill -9 "$OLD_HALIM" 2>/dev/null || true
    echo "   ✅ Old Halim serve stopped"
fi

# ── Kill any stale scalper process ─────────────────────────────────────────
OLD_SCALPER=$(pgrep -f "main.py.*mode scalper" 2>/dev/null || true)
if [ -n "$OLD_SCALPER" ]; then
    echo "🔄 Killing stale scalper (PID $OLD_SCALPER)..."
    kill "$OLD_SCALPER" 2>/dev/null || true
    sleep 1
    kill -0 "$OLD_SCALPER" 2>/dev/null && kill -9 "$OLD_SCALPER" 2>/dev/null || true
    echo "   ✅ Old scalper stopped"
fi

source venv/bin/activate
source scripts/m2_8gb_live_profile.sh

echo "🚀 Starting fresh Halim serve (background)..."
nohup python3 -u halim/halim/serve.py > logs/halim_serve_daemon.log 2>&1 &
HALIM_PID=$!
echo "   Halim PID=$HALIM_PID"

# Wait for model to pre-load (MLX takes 5-20s depending on cache)
echo "   Waiting for model warmup..."
for i in $(seq 1 45); do
    if curl -s -m 2 http://localhost:8765/health 2>/dev/null | grep -q '"ok":true'; then
        echo "   ✅ Halim serve ready (model warmed)"
        echo "   ─── Last lines from daemon log ───"
        tail -5 logs/halim_serve_daemon.log 2>/dev/null || true
        echo ""
        break
    fi
    if [ $((i % 10)) -eq 0 ]; then
        echo "   Still waiting ($i sec)..."
        tail -2 logs/halim_serve_daemon.log 2>/dev/null || true
    fi
    sleep 1
done
echo ""

echo "🚀 Starting HANOON scalper..."
echo "   Stop with Ctrl+C then run: ./stop.sh"
echo ""
python3 -u main.py --mode scalper --port 4002 --client-id 1

echo "🛑 Scalper exited. Run ./stop.sh to clean up Halim serve."
