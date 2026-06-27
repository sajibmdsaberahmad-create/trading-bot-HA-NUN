#!/bin/bash
# Double-click weekend training — IB farm download (client_id=1) + replay auto-loop
# Pace: train (~50ms/step) | pass turbo or realtime as first arg in Terminal
cd "$(dirname "$0")"
chmod +x scripts/weekend_replay_train.sh scripts/stop_weekend_replay.sh \
  weekend_replay_train.sh stop_weekend_replay.sh \
  WEEKEND_REPLAY_STOP.command 2>/dev/null || true
exec ./scripts/weekend_replay_train.sh "${1:-train}"
