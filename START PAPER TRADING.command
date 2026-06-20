#!/bin/bash
# One-click start for PPO Paper Trading (SPY)
cd "$(dirname "$0")"
source venv/bin/activate
python main.py --mode trade --ticker SPY --port 7497