#!/usr/bin/env bash
# Install daily Mac shutdown at 2:00 AM Asia/Dhaka — NOT part of HANOON/trading bot.
set -euo pipefail

LABEL="com.local.scheduled-shutdown-bdt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/$LABEL.plist"
PLIST_DST="/Library/LaunchDaemons/$LABEL.plist"

if [[ ! -f "$PLIST_SRC" ]]; then
  echo "Missing plist: $PLIST_SRC" >&2
  exit 1
fi

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This installer is for macOS only." >&2
  exit 1
fi

echo "Installing scheduled shutdown: every day at 2:00 AM (Mac local time = BDT if timezone is Asia/Dhaka)"
echo "Current timezone: $(readlink /etc/localtime 2>/dev/null || echo unknown)"
date

sudo cp "$PLIST_SRC" "$PLIST_DST"
sudo chown root:wheel "$PLIST_DST"
sudo chmod 644 "$PLIST_DST"

# Reload daemon (macOS 11+)
sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo launchctl bootstrap system "$PLIST_DST"
sudo launchctl enable "system/$LABEL" 2>/dev/null || true

echo ""
echo "Done. Mac will shut down daily at 2:00 AM if it is running then."
echo "Remove with: sudo scripts/mac/uninstall_scheduled_shutdown_bdt.sh"
echo "Log: /var/log/scheduled-shutdown-bdt.log"
