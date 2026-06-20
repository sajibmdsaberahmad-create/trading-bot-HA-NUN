#!/bin/bash
# ONE-CLICK START — auto-detects paper or live IB Gateway
# Just double-click this file. No terminal. No commands.

cd "$(dirname "$0")"
source venv/bin/activate

# Auto-detect which IB Gateway port is open
if lsof -i :4002 >/dev/null 2>&1; then
    PORT=4002
    MODE="PAPER"
elif lsof -i :7496 >/dev/null 2>&1; then
    PORT=7496
    MODE="LIVE"
elif lsof -i :7497 >/dev/null 2>&1; then
    PORT=7497
    MODE="PAPER"
else
    # Default to paper if nothing detected
    PORT=4002
    MODE="PAPER (default)"
fi

echo "=========================================="
echo "  TRADING BOT — $MODE mode (port $PORT)"
echo "=========================================="
echo "  Dashboard:  http://localhost:8501"
echo "  Stop:       Ctrl+C"
echo "=========================================="
echo ""

# Start dashboard in background
streamlit run dashboard/app.py --server.headless true >/dev/null 2>&1 &
DASH_PID=$!

# Start trading bot (scalper is default)
python main.py --mode scalper --port $PORT --client-id 1

# Cleanup dashboard when bot stops
kill $DASH_PID 2>/dev/null