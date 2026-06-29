#!/usr/bin/env bash
# Install daily Mac shutdown at 2:05 AM Asia/Dhaka — no sudo required.
set -euo pipefail

LABEL="com.local.scheduled-shutdown-bdt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HANOON_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/HANOON-mac-shutdown"
AGENTS_DIR="$HOME/Library/LaunchAgents"
WRAPPER_DST="$SUPPORT_DIR/graceful_mac_shutdown_bdt.sh"
PLIST_DST="$AGENTS_DIR/$LABEL.plist"
TEMPLATE_WRAPPER="$SCRIPT_DIR/graceful_mac_shutdown_bdt.sh"
UID_NUM="$(id -u)"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS only." >&2
  exit 1
fi

mkdir -p "$SUPPORT_DIR" "$AGENTS_DIR"

sed "s|__HANOON_ROOT__|$HANOON_ROOT|g" "$TEMPLATE_WRAPPER" > "$WRAPPER_DST"
chmod 755 "$WRAPPER_DST"

cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>Comment</key>
	<string>2:05 AM Asia/Dhaka — graceful HANOON stop, then Mac shutdown</string>
	<key>ProgramArguments</key>
	<array>
		<string>$WRAPPER_DST</string>
	</array>
	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>2</integer>
		<key>Minute</key>
		<integer>5</integer>
	</dict>
	<key>StandardOutPath</key>
	<string>$HOME/Library/Logs/scheduled-shutdown-bdt.log</string>
	<key>StandardErrorPath</key>
	<string>$HOME/Library/Logs/scheduled-shutdown-bdt.log</string>
	<key>RunAtLoad</key>
	<false/>
</dict>
</plist>
EOF

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || \
  launchctl unload "$PLIST_DST" 2>/dev/null || true

if launchctl bootstrap "gui/$UID_NUM" "$PLIST_DST" 2>/dev/null; then
  :
elif launchctl load "$PLIST_DST" 2>/dev/null; then
  :
else
  echo "Failed to load LaunchAgent." >&2
  exit 1
fi

launchctl enable "gui/$UID_NUM/$LABEL" 2>/dev/null || true

echo "Installed (no sudo required)."
echo "  HANOON root: $HANOON_ROOT"
echo "  Timezone:    $(readlink /etc/localtime 2>/dev/null || echo unknown)"
echo "  Schedule:    2:05 AM local (= BDT if Asia/Dhaka)"
echo "  Wrapper:     $WRAPPER_DST"
echo "  Plist:       $PLIST_DST"
echo "  Log:         $HOME/Library/Logs/scheduled-shutdown-bdt.log"
echo ""
echo "Flow: stop_hanoon → halim_stop → 10s → Mac shut down"
echo "Uninstall: $SCRIPT_DIR/uninstall_scheduled_shutdown_bdt.sh"
