#!/usr/bin/env bash
set -euo pipefail

LABEL_USER="com.local.scheduled-shutdown-bdt"
LABEL_ROOT="com.local.scheduled-shutdown-bdt-poweroff"
UID_NUM="$(id -u)"
USER_PLIST="$HOME/Library/LaunchAgents/$LABEL_USER.plist"
ROOT_PLIST="/Library/LaunchDaemons/$LABEL_ROOT.plist"
POWER_WRAPPER="/usr/local/bin/mac-poweroff-bdt"
SUPPORT_DIR="$HOME/Library/Application Support/HANOON-mac-shutdown"

launchctl bootout "gui/$UID_NUM/$LABEL_USER" 2>/dev/null || \
  launchctl unload "$USER_PLIST" 2>/dev/null || true
rm -f "$USER_PLIST" "$SUPPORT_DIR/graceful_hanoon_stop_bdt.sh" \
      "$SUPPORT_DIR/graceful_mac_shutdown_bdt.sh"

if sudo -n true 2>/dev/null || [[ -t 0 ]]; then
  sudo launchctl bootout "system/$LABEL_ROOT" 2>/dev/null || true
  sudo rm -f "$ROOT_PLIST" "$POWER_WRAPPER" 2>/dev/null || true
fi

echo "Removed scheduled shutdown jobs."
