#!/bin/bash
cd "$(dirname "$0")"
chmod +x scripts/stop_replay.sh stop_replay.sh REPLAY_STOP.command 2>/dev/null || true
# Full multi-ticker HANOON replay — same ScalperRunner as live
#   train (default ~50ms/step) | realtime | turbo
exec ./scripts/start_replay_live.sh "${1:-train}"
