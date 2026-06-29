#!/usr/bin/env bash
# 2:12 AM — Mac power off via System Events (logged-in, screen lock OK).
LOG="$HOME/Library/Logs/scheduled-shutdown-bdt.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') === 2:12 AM — user power off (screen lock OK) ==="
  /usr/bin/osascript -e 'tell application "System Events" to shut down'
  echo "osascript exit: $?"
} >>"$LOG" 2>&1
