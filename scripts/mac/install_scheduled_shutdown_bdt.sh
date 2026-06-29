#!/usr/bin/env bash
# Install daily Mac shutdown at 2:05 AM Asia/Dhaka (graceful HANOON stop first).
# Separate from the trading bot — one-time sudo required.
set -euo pipefail

LABEL="com.local.scheduled-shutdown-bdt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HANOON_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MAC_USER="$(whoami)"
WRAPPER_DST="/usr/local/bin/graceful-mac-shutdown-bdt"
PLIST_DST="/Library/LaunchDaemons/$LABEL.plist"
TEMPLATE_PLIST="$SCRIPT_DIR/$LABEL.plist"
TEMPLATE_WRAPPER="$SCRIPT_DIR/graceful_mac_shutdown_bdt.sh"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS only." >&2
  exit 1
fi

echo "HANOON root: $HANOON_ROOT"
echo "Mac user:    $MAC_USER"
echo "Timezone:    $(readlink /etc/localtime 2>/dev/null || echo unknown)"
echo "Schedule:    2:05 AM local (= BDT if timezone is Asia/Dhaka)"
echo ""
echo "Flow at 2:05 AM:"
echo "  1. stop_hanoon.sh (graceful — gold, evolution, git; up to 180s)"
echo "  2. halim_stop.sh"
echo "  3. 10s buffer"
echo "  4. Mac powers off"
echo ""

# Build wrapper with paths baked in
sudo mkdir -p /usr/local/bin
sed -e "s|__HANOON_ROOT__|$HANOON_ROOT|g" \
    -e "s|__MAC_USER__|$MAC_USER|g" \
    "$TEMPLATE_WRAPPER" | sudo tee "$WRAPPER_DST" >/dev/null
sudo chmod 755 "$WRAPPER_DST"

# Build plist with wrapper path
sed "s|__WRAPPER_PATH__|$WRAPPER_DST|g" "$TEMPLATE_PLIST" | sudo tee "$PLIST_DST" >/dev/null
sudo chown root:wheel "$PLIST_DST"
sudo chmod 644 "$PLIST_DST"

sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo launchctl bootstrap system "$PLIST_DST"
sudo launchctl enable "system/$LABEL" 2>/dev/null || true

echo ""
echo "Installed."
echo "  Wrapper: $WRAPPER_DST"
echo "  Plist:   $PLIST_DST"
echo "  Log:     /var/log/scheduled-shutdown-bdt.log"
echo ""
echo "Test bot stop only (no Mac shutdown):"
echo "  SHUTDOWN_WAIT_SEC=60 $HANOON_ROOT/scripts/stop_hanoon.sh"
echo ""
echo "Uninstall: sudo $SCRIPT_DIR/uninstall_scheduled_shutdown_bdt.sh"
echo ""
echo "To use 2:02 instead of 2:05, edit Minute in $PLIST_DST then:"
echo "  sudo launchctl bootout system/$LABEL"
echo "  sudo launchctl bootstrap system $PLIST_DST"
