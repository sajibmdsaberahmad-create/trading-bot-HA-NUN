#!/usr/bin/env bash
# Install nightly shutdown — works when screen is LOCKED (user logged in).
#   2:05 AM  user agent  → graceful HANOON + Halim stop
#   2:12 AM  root daemon → guaranteed Mac power off
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HANOON_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL_STOP="com.local.scheduled-shutdown-bdt"
LABEL_POWER_USER="com.local.scheduled-shutdown-bdt-poweroff-user"
LABEL_POWER_ROOT="com.local.scheduled-shutdown-bdt-poweroff"
UID_NUM="$(id -u)"
SUPPORT_DIR="$HOME/Library/Application Support/HANOON-mac-shutdown"
AGENTS_DIR="$HOME/Library/LaunchAgents"
STOP_WRAPPER="$SUPPORT_DIR/graceful_hanoon_stop_bdt.sh"
POWER_USER_WRAPPER="$SUPPORT_DIR/mac_poweroff_user_bdt.sh"
POWER_ROOT_WRAPPER="/usr/local/bin/mac-poweroff-bdt"
STOP_PLIST="$AGENTS_DIR/$LABEL_STOP.plist"
POWER_USER_PLIST="$AGENTS_DIR/$LABEL_POWER_USER.plist"
ROOT_PLIST="/Library/LaunchDaemons/$LABEL_POWER_ROOT.plist"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS only." >&2
  exit 1
fi

echo "Timezone: $(readlink /etc/localtime 2>/dev/null || echo unknown)"
echo "Schedule:"
echo "  2:05 AM — stop HANOON + Halim (screen lock OK)"
echo "  2:12 AM — Mac powers off (screen lock OK)"
echo ""

# ── 1. User LaunchAgent: graceful bot stop at 2:05 ──
mkdir -p "$SUPPORT_DIR" "$AGENTS_DIR"
sed "s|__HANOON_ROOT__|$HANOON_ROOT|g" "$SCRIPT_DIR/graceful_hanoon_stop_bdt.sh" > "$STOP_WRAPPER"
chmod 755 "$STOP_WRAPPER"
cp "$SCRIPT_DIR/mac_poweroff_user_bdt.sh" "$POWER_USER_WRAPPER"
chmod 755 "$POWER_USER_WRAPPER"

cat > "$STOP_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL_STOP</string>
	<key>Comment</key>
	<string>2:05 AM BDT — graceful HANOON stop (screen lock OK)</string>
	<key>ProgramArguments</key>
	<array>
		<string>$STOP_WRAPPER</string>
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

launchctl bootout "gui/$UID_NUM/$LABEL_STOP" 2>/dev/null || \
  launchctl unload "$STOP_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$STOP_PLIST" 2>/dev/null || \
  launchctl load "$STOP_PLIST"
launchctl enable "gui/$UID_NUM/$LABEL_STOP" 2>/dev/null || true
echo "✓ Bot stop job (2:05 AM)"

# ── 2. User LaunchAgent: power off at 2:12 (screen lock OK, no sudo) ──
cat > "$POWER_USER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL_POWER_USER</string>
	<key>Comment</key>
	<string>2:12 AM BDT — Mac shut down (logged in, screen lock OK)</string>
	<key>ProgramArguments</key>
	<array>
		<string>$POWER_USER_WRAPPER</string>
	</array>
	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>2</integer>
		<key>Minute</key>
		<integer>12</integer>
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

launchctl bootout "gui/$UID_NUM/$LABEL_POWER_USER" 2>/dev/null || \
  launchctl unload "$POWER_USER_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$POWER_USER_PLIST" 2>/dev/null || \
  launchctl load "$POWER_USER_PLIST"
launchctl enable "gui/$UID_NUM/$LABEL_POWER_USER" 2>/dev/null || true
echo "✓ Mac power-off job (2:12 AM, screen lock OK)"

# ── 3. Optional root LaunchDaemon: guaranteed power off if sudo available ──
if sudo -n true 2>/dev/null; then
  SUDO="sudo"
elif [[ -t 0 ]]; then
  SUDO="sudo"
else
  echo ""
  echo "ℹ️  Optional root power-off skipped (no sudo). User 2:12 job is enough for most Macs."
  echo ""
  echo "Done. Logged in + screen locked is OK."
  echo "  Log: $HOME/Library/Logs/scheduled-shutdown-bdt.log"
  exit 0
fi

$SUDO mkdir -p /usr/local/bin
$SUDO cp "$SCRIPT_DIR/mac_poweroff_bdt.sh" "$POWER_ROOT_WRAPPER"
$SUDO chmod 755 "$POWER_ROOT_WRAPPER"

$SUDO tee "$ROOT_PLIST" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL_POWER_ROOT</string>
	<key>Comment</key>
	<string>2:12 AM BDT — Mac power off (root, screen lock OK)</string>
	<key>ProgramArguments</key>
	<array>
		<string>$POWER_ROOT_WRAPPER</string>
	</array>
	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>2</integer>
		<key>Minute</key>
		<integer>12</integer>
	</dict>
	<key>StandardOutPath</key>
	<string>/var/log/scheduled-shutdown-bdt.log</string>
	<key>StandardErrorPath</key>
	<string>/var/log/scheduled-shutdown-bdt.log</string>
	<key>RunAtLoad</key>
	<false/>
</dict>
</plist>
EOF

$SUDO chown root:wheel "$ROOT_PLIST"
$SUDO chmod 644 "$ROOT_PLIST"
$SUDO launchctl bootout "system/$LABEL_POWER_ROOT" 2>/dev/null || true
$SUDO launchctl bootstrap system "$ROOT_PLIST"
$SUDO launchctl enable "system/$LABEL_POWER_ROOT" 2>/dev/null || true
echo "✓ Optional root power-off backup installed"

echo ""
echo "Done. Logged in + screen locked is fine."
echo "  User log:  $HOME/Library/Logs/scheduled-shutdown-bdt.log"
echo "  Root log:  /var/log/scheduled-shutdown-bdt.log"
echo "  Uninstall: $SCRIPT_DIR/uninstall_scheduled_shutdown_bdt.sh"
