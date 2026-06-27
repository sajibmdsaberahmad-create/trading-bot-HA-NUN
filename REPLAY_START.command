#!/bin/bash
# Double-click launcher (macOS) — full HANOON replay (multi-ticker train pace)
cd "$(dirname "$0")"
chmod +x scripts/stop_replay.sh stop_replay.sh REPLAY_STOP.command scripts/start_replay_live.sh 2>/dev/null || true
./scripts/start_replay_live.sh "${1:-train}"
EXIT=$?
if [[ $EXIT -ne 0 ]]; then
  echo ""
  echo "❌ Replay did not start (exit $EXIT). See messages above."
  echo "   Log: logs/REPLAY_SCALPER.log"
  echo ""
  echo "Press Enter to close this window…"
  read -r _
fi
exit $EXIT
