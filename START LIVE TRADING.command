#!/bin/bash
# One-click start for LIVE TRADING (real money)
cd "$(dirname "$0")"
source venv/bin/activate
python main.py --mode trade --ticker SPY --port 7496