#!/usr/bin/env bash
# max_perf.sh — Kill every non-essential app so the bot gets all RAM.
# Run BEFORE ./start.sh. Closes Cursor, browsers, office apps,
# media apps, and any user apps that eat RAM.
#
# Usage:  source scripts/max_perf.sh   (recommended)
#         bash scripts/max_perf.sh
#
# Safe: only kills user-space GUI apps. Never kills system processes,
# Halim serve, or the scalper runner.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🧹 Max Performance Mode — killing non-essential apps...${NC}"
echo ""

KILLED=0
FREED_MB=0

_estimate_mb() {
    local pid="$1"
    local mem=0
    mem=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ' || echo 0)
    echo "$((mem / 1024))"
}

_kill_app() {
    local app="$1"
    local pids
    pids=$(pgrep -i "$app" 2>/dev/null || true)
    if [ -z "$pids" ]; then
        return
    fi
    for pid in $pids; do
        local est=$(_estimate_mb "$pid")
        # Skip if this process is the script itself or the shell
        if [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ]; then
            continue
        fi
        # Kill gently first, then force
        kill "$pid" 2>/dev/null || true
        sleep 0.1
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        KILLED=$((KILLED + 1))
        FREED_MB=$((FREED_MB + est))
        echo -e "  ${RED}✕${NC} $app (PID $pid) ≈ ${est}MB"
    done
}

# ── IDEs and editors (biggest RAM hogs) ──
_kill_app "Cursor"
_kill_app "Code"          # VS Code
_kill_app "Code - Insiders"
_kill_app "Xcode"
_kill_app "Android Studio"
_kill_app "IntelliJ"
_kill_app "PyCharm"
_kill_app "Sublime Text"
_kill_app "TextMate"

# ── Browsers ──
_kill_app "Safari"
_kill_app "Google Chrome"
_kill_app "Firefox"
_kill_app "Brave Browser"
_kill_app "Opera"
_kill_app "Edge"
_kill_app "Chromium"

# ── Office / productivity ──
_kill_app "Microsoft Word"
_kill_app "Microsoft Excel"
_kill_app "Microsoft PowerPoint"
_kill_app "Microsoft Outlook"
_kill_app "Microsoft Teams"
_kill_app "Slack"
_kill_app "Discord"
_kill_app "Notion"
_kill_app "Obsidian"
_kill_app "Evernote"
_kill_app "Todoist"
_kill_app "Linear"
_kill_app "Figma"

# ── Media / entertainment ──
_kill_app "Spotify"
_kill_app "Apple Music"
_kill_app "Music"
_kill_app "Podcasts"
_kill_app "TV"
_kill_app "VLC"
_kill_app "IINA"
_kill_app "QuickTime Player"
_kill_app "Photos"

# ── Communication ──
_kill_app "WhatsApp"
_kill_app "Telegram"
_kill_app "Signal"
_kill_app "Messenger"
_kill_app "Zoom"
_kill_app "Meeting Center"
_kill_app "WebEx"
_kill_app "Skype"

# ── Utilities that chew RAM ──
_kill_app "Docker"
_kill_app "VirtualBox"
_kill_app "VMware"
_kill_app "Parallels"
_kill_app "iTerm2"
_kill_app "Terminal"       # Kill other terminals (keep current shell)
_kill_app "Warp"
_kill_app "TablePlus"
_kill_app "Postman"
_kill_app "DBeaver"
_kill_app "Sequel Ace"
_kill_app "Miniforge"
_kill_app "Anaconda"
_kill_app "Jupyter"

# ── macOS default apps that aren't needed ──
_kill_app "Calendar"
_kill_app "Reminders"
_kill_app "Notes"
_kill_app "Maps"
_kill_app "News"
_kill_app "Weather"
_kill_app "Stocks"
_kill_app "VoiceMemos"

echo ""
echo -e "${GREEN}✅ Done.${NC}"
echo "   Killed $KILLED processes, freed ≈ ${FREED_MB}MB RAM"
echo ""

# Show available RAM
AVAIL=$(memory_pressure 2>/dev/null | rg 'Pages free' | awk '{print $3}')
if [ -n "$AVAIL" ]; then
    FREE_MB=$((AVAIL * 16384 / 1024 / 1024))
    echo -e "   Free RAM now: ${FREE_MB}MB"
else
    vm_stat 2>/dev/null | head -3
fi

echo ""
echo -e "${GREEN}🚀 Ready to run: ./start.sh${NC}"
