#!/usr/bin/env bash
# 2:12 AM — guaranteed Mac power off (root; works when screen locked, user logged in).
LOG="/var/log/scheduled-shutdown-bdt.log"
MARKER="/tmp/hanoon-graceful-stop-done"

{
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') === 2:12 AM — Mac power off ==="
  if [[ -f "$MARKER" ]]; then
    echo "Graceful HANOON stop marker found — proceeding"
  else
    echo "No HANOON stop marker — bot may not have run; shutting down anyway"
  fi
  echo "Calling /sbin/shutdown -h now"
} >>"$LOG" 2>&1

/sbin/shutdown -h now
