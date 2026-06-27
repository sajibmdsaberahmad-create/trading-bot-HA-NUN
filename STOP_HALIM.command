#!/bin/bash
# Double-click launcher (macOS) — stop Halim serve + Telegram listener
cd "$(dirname "$0")"
chmod +x scripts/halim_stop.sh STOP_HALIM.command 2>/dev/null || true
exec ./scripts/halim_stop.sh
