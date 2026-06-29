#!/usr/bin/env bash
# Graceful nightly Mac shutdown — stops HANOON first, then powers off.
# Installed by scripts/mac/install_scheduled_shutdown_bdt.sh (not part of the algo loop).
set -u

HANOON_ROOT="__HANOON_ROOT__"
MAC_USER="__MAC_USER__"
LOG="/var/log/scheduled-shutdown-bdt.log"
MAX_BOT_WAIT="${SHUTDOWN_WAIT_SEC:-180}"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') $*" | tee -a "$LOG"
}

log "=== Scheduled Mac shutdown started (graceful HANOON first) ==="

if [[ -d "$HANOON_ROOT" ]]; then
  if [[ -x "$HANOON_ROOT/scripts/stop_hanoon.sh" ]]; then
    log "Stopping HANOON (up to ${MAX_BOT_WAIT}s)…"
    if [[ "$(id -u)" -eq 0 ]]; then
      sudo -u "$MAC_USER" env SHUTDOWN_WAIT_SEC="$MAX_BOT_WAIT" \
        "$HANOON_ROOT/scripts/stop_hanoon.sh" >>"$LOG" 2>&1 || true
    else
      SHUTDOWN_WAIT_SEC="$MAX_BOT_WAIT" \
        "$HANOON_ROOT/scripts/stop_hanoon.sh" >>"$LOG" 2>&1 || true
    fi
    log "HANOON stop script finished"
  else
    log "stop_hanoon.sh not found — skipping bot stop"
  fi

  if [[ -x "$HANOON_ROOT/scripts/halim_stop.sh" ]]; then
    log "Stopping Halim serve…"
    if [[ "$(id -u)" -eq 0 ]]; then
      sudo -u "$MAC_USER" "$HANOON_ROOT/scripts/halim_stop.sh" >>"$LOG" 2>&1 || true
    else
      "$HANOON_ROOT/scripts/halim_stop.sh" >>"$LOG" 2>&1 || true
    fi
  fi
else
  log "HANOON root missing ($HANOON_ROOT) — skipping bot stop"
fi

log "Extra buffer before Mac power off (10s)…"
sleep 10

log "Powering Mac off now"
/sbin/shutdown -h now
