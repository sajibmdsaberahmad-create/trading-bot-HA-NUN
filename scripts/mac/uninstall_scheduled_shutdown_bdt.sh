#!/usr/bin/env bash
set -euo pipefail

LABEL="com.local.scheduled-shutdown-bdt"
PLIST_DST="/Library/LaunchDaemons/$LABEL.plist"
WRAPPER_DST="/usr/local/bin/graceful-mac-shutdown-bdt"

sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo rm -f "$PLIST_DST" "$WRAPPER_DST"
echo "Removed scheduled shutdown ($LABEL)."
