#!/bin/bash
# Double-click launcher (macOS) — Halim serve + Telegram chat (no trading algo)
cd "$(dirname "$0")"
chmod +x scripts/halim_start.sh scripts/halim_stop.sh scripts/ensure_halim_active.sh START_HALIM.command STOP_HALIM.command 2>/dev/null || true
exec ./scripts/halim_start.sh
