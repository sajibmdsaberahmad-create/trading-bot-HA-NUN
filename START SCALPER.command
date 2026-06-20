#!/bin/bash
# One-click start for Institutional Scalper (default/main mode)
cd "$(dirname "$0")"
source venv/bin/activate
python main.py --mode scalper --port 4002