#!/usr/bin/env bash
set -euo pipefail

LABEL_STOP="com.local.scheduled-shutdown-bdt"
LABEL_POWER_USER="com.local.scheduled-shutdown-bdt-poweroff-user"
LABEL_POWER_ROOT="com.local.scheduled-shutdown-bdt-poweroff"
UID_NUM="$(id -u)"
STOP_PLIST="$HOME/Library/LaunchAgents/$LABEL_STOP.plist"
POWER_USER_PLIST="$HOME/Library/LaunchAgents/$LABEL_POWER_USER.plist"
ROOT_PLIST="/Library/LaunchDaemons/$LABEL_POWER_ROOT.plist"
POWER_ROOT_WRAPPER="/usr/local/bin/mac-poweroff-bdt"
SUPPORT_DIR="$HOME/Library/Application Support/HANOON-mac-shutdown"

launchctl bootout "gui/$UID_NUM/$LABEL_STOP" 2>/dev/null || \
  launchctl unload "$STOP_PLIST" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$LABEL_POWER_USER" 2>/dev/null || \
  launchctl unload "$POWER_USER_PLIST" 2>/dev/null || true
rm -f "$STOP_PLIST" "$POWER_USER_PLIST" \
      "$SUPPORT_DIR/graceful_hanoon_stop_bdt.sh" \
      "$SUPPORT_DIR/mac_poweroff_user_bdt.sh" \
      "$SUPPORT_DIR/graceful_mac_shutdown_bdt.sh"

if sudo -n true 2>/dev/null || [[ -t 0 ]]; then
  sudo launchctl bootout "system/$LABEL_POWER_ROOT" 2>/dev/null || true
  sudo rm -f "$ROOT_PLIST" "$POWER_ROOT_WRAPPER" 2>/dev/null || true
fi

echo "Removed scheduled shutdown jobs."
