#!/usr/bin/env bash
set -euo pipefail

LABEL="com.local.scheduled-shutdown-bdt"
PLIST_DST="/Library/LaunchDaemons/$LABEL.plist"

sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo rm -f "$PLIST_DST"
echo "Removed scheduled shutdown ($LABEL)."
