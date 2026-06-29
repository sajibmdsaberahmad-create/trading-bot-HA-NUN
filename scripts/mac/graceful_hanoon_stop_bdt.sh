#!/usr/bin/env bash
# 2:05 AM — graceful HANOON + Halim stop (works logged-in with screen locked).
set -u

HANOON_ROOT="__HANOON_ROOT__"
LOG="$HOME/Library/Logs/scheduled-shutdown-bdt.log"
MAX_BOT_WAIT="${SHUTDOWN_WAIT_SEC:-180}"
MARKER="/tmp/hanoon-graceful-stop-done"

mkdir -p "$(dirname "$LOG")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') $*" | tee -a "$LOG"
}

log "=== 2:05 AM — graceful HANOON stop (screen lock OK) ==="

if [[ -d "$HANOON_ROOT" ]]; then
  if [[ -x "$HANOON_ROOT/scripts/stop_hanoon.sh" ]]; then
    log "Stopping HANOON (up to ${MAX_BOT_WAIT}s)…"
    SHUTDOWN_WAIT_SEC="$MAX_BOT_WAIT" \
      "$HANOON_ROOT/scripts/stop_hanoon.sh" >>"$LOG" 2>&1 || true
    log "HANOON stop finished"
  fi
  if [[ -x "$HANOON_ROOT/scripts/halim_stop.sh" ]]; then
    log "Stopping Halim…"
    "$HANOON_ROOT/scripts/halim_stop.sh" >>"$LOG" 2>&1 || true
  fi
else
  log "HANOON root missing — skip bot stop"
fi

date > "$MARKER"
log "Bot stop complete — Mac will power off at 2:12 AM (LaunchDaemon, works when screen locked)"
