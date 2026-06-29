#!/bin/bash
# Double-click — Halim web learn (OFF-HOURS ONLY on 8GB Mac; auto-stops when HANOON starts).
cd "$(dirname "$0")"
chmod +x scripts/halim_learn_browse.sh LEARN_START.command 2>/dev/null || true
export HALIM_LEARN_LOOP=true
if ! PYTHONPATH="$(pwd)/halim:$(pwd)" python3 -c "
from core.device_trading_focus import learn_blocked_for_device_focus
import sys
b = learn_blocked_for_device_focus()
if b:
    print(b.get('message', 'Learn blocked during market hours.'))
    sys.exit(1)
"; then
  read -r -p "Press Enter to close…" _ 2>/dev/null || true
  exit 0
fi
exec ./scripts/halim_learn_browse.sh
