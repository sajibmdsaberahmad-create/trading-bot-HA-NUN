#!/usr/bin/env bash
set -euo pipefail

LABEL="com.local.scheduled-shutdown-bdt"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
WRAPPER_DST="$HOME/Library/Application Support/HANOON-mac-shutdown/graceful_mac_shutdown_bdt.sh"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || \
  launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f "$PLIST_DST" "$WRAPPER_DST"
echo "Removed scheduled shutdown ($LABEL)."
