#!/bin/bash
# Double-click stop — weekend replay loop + active replay + optional git sync
cd "$(dirname "$0")"
chmod +x scripts/stop_weekend_replay.sh stop_weekend_replay.sh \
  scripts/weekend_replay_train.sh weekend_replay_train.sh 2>/dev/null || true
exec ./scripts/stop_weekend_replay.sh
