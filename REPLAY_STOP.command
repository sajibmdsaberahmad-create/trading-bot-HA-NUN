#!/bin/bash
# Double-click graceful replay stop (macOS) — Halim gold + evolution + git sync
# If weekend loop is running, also stops auto-restart (or use WEEKEND_REPLAY_STOP.command).
cd "$(dirname "$0")"
chmod +x scripts/stop_replay.sh stop_replay.sh 2>/dev/null || true
exec ./scripts/stop_replay.sh
