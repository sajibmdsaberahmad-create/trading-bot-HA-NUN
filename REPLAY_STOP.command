#!/bin/bash
# Double-click graceful replay stop (macOS) — Halim gold + evolution + git sync
cd "$(dirname "$0")"
chmod +x scripts/stop_replay.sh stop_replay.sh 2>/dev/null || true
exec ./scripts/stop_replay.sh
